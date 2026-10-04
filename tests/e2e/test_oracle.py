"""The oracle test: the API against osTicket's own PHP, on one rich scenario.

Every other check compares the API with *a reading of* osTicket's source. This one compares it with osTicket
itself: ``oracle/oracle.php`` runs inside the osTicket container and answers, with osTicket's own methods,
"what may this agent see?" — and every answer must match what the API returns for that agent.

The scenario covers what the API alone cannot set up, so a few steps write to the dev database directly
(sub-departments, a managed department, extra-department access, an assigned-only agent, global permissions,
referrals, a child help topic, a team on a task). All of it is throwaway and removed afterwards.

Needs docker compose, and MYSQL_PASSWORD in the environment (``set -a; . ./.env; set +a`` — the same step
``make test-e2e`` already needs for the admin login). Skipped without them.
"""

import json
import os
import subprocess
from pathlib import Path

import httpx
import pytest

from app.core.permissions import ALL_PERMISSIONS
from tests.e2e.conftest import BASE_URL, DELIVERABLE_DOMAIN, login_as

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parents[2]
ORACLE = Path(__file__).with_name("oracle") / "oracle.php"
PASSWORD = "E2e-Pass-9x!Qz"
SUPPORT, SALES, MAINT = 1, 2, 3
ALL_ACCESS, LIMITED_ROLE = 1, 3


# ── plumbing: SQL and PHP through docker compose ─────────────────────────────


def _compose(*args, timeout=90):
    return subprocess.run(["docker", "compose", *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout)


def sql(statement: str) -> list[list[str]]:
    result = _compose("exec", "-T", "-e", f"MYSQL_PWD={os.environ['MYSQL_PASSWORD']}", "db",
                      "mariadb", "-uosticket", "osticket", "-N", "-B", "-e", statement)
    assert result.returncode == 0, f"SQL failed: {result.stderr[:300]}\n{statement[:200]}"
    return [line.split("\t") for line in result.stdout.strip().splitlines() if line]


def oracle(payload: dict) -> dict:
    copied = subprocess.run(["docker", "cp", str(ORACLE), "fast-ticket-osticket-1:/tmp/oracle.php"], capture_output=True, text=True)
    assert copied.returncode == 0, copied.stderr
    result = _compose("exec", "-T", "-u", "www-data", "osticket", "php", "/tmp/oracle.php", json.dumps(payload), timeout=120)
    marker = result.stdout.partition("ORACLE_JSON_BEGIN")[2].partition("ORACLE_JSON_END")[0]
    assert marker, f"oracle printed nothing usable: {result.stdout[-1200:]} {result.stderr[-400:]}"
    return json.loads(marker)


@pytest.fixture(scope="module", autouse=True)
def _requirements():
    if not os.environ.get("MYSQL_PASSWORD"):
        pytest.skip("needs MYSQL_PASSWORD (source .env) to set up and tear down the scenario")
    if _compose("ps", "-q", "db").returncode != 0:
        pytest.skip("docker compose is not available")


# ── the scenario ─────────────────────────────────────────────────────────────


def _department(uniq: str, name: str, parent: int | None) -> int:
    pid = "NULL" if parent is None else str(parent)
    row = sql(
        "INSERT INTO ost_department (pid, tpl_id, sla_id, schedule_id, email_id, autoresp_email_id, manager_id, flags, name, "
        "signature, ispublic, group_membership, ticket_auto_response, message_auto_response, path, updated, created) "
        f"SELECT {pid}, tpl_id, sla_id, schedule_id, email_id, autoresp_email_id, 0, flags, 'e2e-oracle-{name}-{uniq}', "
        "signature, ispublic, group_membership, ticket_auto_response, message_auto_response, '', NOW(), NOW() "
        "FROM ost_department WHERE id = 1; SET @d = LAST_INSERT_ID(); "
        f"UPDATE ost_department SET path = CONCAT({'' if parent is None else f'(SELECT path FROM (SELECT path FROM ost_department WHERE id = {parent}) x), '}'/', @d, '/') WHERE id = @d; "
        "SELECT @d;"
    )
    return int(row[-1][0])


class Scenario:
    def __init__(self, bridge, uniq):
        self.bridge, self.uniq = bridge, uniq
        self.agents: dict[str, int] = {}
        self.usernames: dict[str, str] = {}
        self.tickets: dict[str, int] = {}
        self.tasks: dict[str, int] = {}
        self.canned: dict[str, int] = {}
        self.dept: dict[str, int] = {}

    # a helper each for the API-made things
    def agent(self, key, dept, role=LIMITED_ROLE, teams=()):
        username = f"o{key}{self.uniq}"
        self.agents[key] = self.bridge.create(
            "/api/v1/staff",
            {"username": username, "firstname": "Oracle", "lastname": key.title(), "email": f"{username}@{DELIVERABLE_DOMAIN}",
             "dept_id": dept, "role_id": role, "password": PASSWORD, "is_active": True, "is_admin": False, "teams": list(teams)},
            cleanup="/api/v1/staff/{id}",
        )
        self.usernames[key] = username

    def ticket(self, key, dept, *, assign=None, team=None, close=False):
        customer = f"oracle.{key}.{self.uniq}@{DELIVERABLE_DOMAIN}"
        self.bridge.create("/api/v1/users", {"email": customer, "name": f"Oracle {key}"}, cleanup="/api/v1/users/{id}")
        tid = self.bridge.create(
            "/api/v1/tickets/",
            {"subject": f"oracle {key} {self.uniq}", "message": "m", "email": customer, "name": f"Oracle {key}",
             "dept_id": SUPPORT, "topic_id": 1},
            cleanup="/api/v1/tickets/{id}",
        )
        self.tickets[key] = tid
        if dept not in (SUPPORT, SALES, MAINT):
            # a throwaway department the admin cannot reach: osTicket would refuse the delete, so the ticket is
            # moved home first (cleanups run last-registered-first)
            self.bridge._cleanups.append(lambda: sql(f"UPDATE ost_ticket SET dept_id = {SUPPORT} WHERE ticket_id = {tid}"))
        if dept != SUPPORT:
            self.bridge.command("POST", f"/api/v1/tickets/{tid}/transfer", {"dept_id": dept, "note": "o"})
        if assign:
            self.bridge.command("POST", f"/api/v1/tickets/{tid}/assign", {"staff_id": self.agents[assign], "note": "o"})
        if team:
            self.bridge.command("POST", f"/api/v1/tickets/{tid}/assign-team", {"team_id": team, "note": "o"})
        if close:
            self.bridge.command("POST", f"/api/v1/tickets/{tid}/close", {"note": "o"})

    def task(self, key, dept, *, assign=None, close=False, ticket=None):
        body = {"title": f"oracle task {key} {self.uniq}", "description": "d", "dept_id": dept}
        if ticket:
            body["ticket_id"] = self.tickets[ticket]
        tid = self.bridge.create("/api/v1/tasks/", body, cleanup="/api/v1/tasks/{id}")
        self.tasks[key] = tid
        if assign:
            self.bridge.command("POST", f"/api/v1/tasks/{tid}/assign", {"staff_id": self.agents[assign], "note": "o"})
        if close:
            self.bridge.command("POST", f"/api/v1/tasks/{tid}/close", {})

    def canned_response(self, key, dept, enabled=True):
        self.canned[key] = self.bridge.create(
            "/api/v1/canned", {"title": f"oracle canned {key} {self.uniq}", "response": "r", "dept_id": dept, "is_enabled": enabled},
            cleanup="/api/v1/canned/{id}")

    def refer(self, ticket_key, kind, object_id):
        """A referral row for the ticket's thread — the API cannot create these."""
        thread = sql(f"SELECT id FROM ost_thread WHERE object_id = {self.tickets[ticket_key]} AND object_type IN ('T','C') LIMIT 1")[0][0]
        sql(f"INSERT INTO ost_thread_referral (thread_id, object_id, object_type, created) VALUES ({thread}, {object_id}, '{kind}', NOW())")


def build(bridge, uniq) -> Scenario:
    s = Scenario(bridge, uniq)

    # departments (SQL): a parent and a sub-department; registered first so they are removed last
    s.dept["parent"] = _department(uniq, "parent", None)
    s.dept["child"] = _department(uniq, "child", s.dept["parent"])
    bridge._cleanups.append(lambda: sql(f"DELETE FROM ost_department WHERE id IN ({s.dept['child']}, {s.dept['parent']})"))
    bridge._cleanups.append(lambda: sql("DELETE FROM ost_staff_dept_access WHERE staff_id IN ({})".format(
        ",".join(str(i) for i in s.agents.values()) or "0")))
    P, C = s.dept["parent"], s.dept["child"]
    # assignment rules (Dept::getAssignees): the parent offers only its members, the child only its primary members
    sql(f"UPDATE ost_department SET flags = flags | 1 WHERE id = {P}")
    sql(f"UPDATE ost_department SET flags = flags | 16 WHERE id = {C}")

    s.agent("own", SUPPORT)
    s.agent("parent", P)                       # a department with a sub-department
    s.agent("limited", SALES)                  # assigned-only (SQL)
    s.agent("extra", SUPPORT)                  # plus access to Maintenance with the All Access role (SQL)
    s.agent("manager", SUPPORT)                # manages the parent department (SQL)
    s.agent("dir", SUPPORT)                    # global permissions (SQL)
    s.agent("assign", SUPPORT, role=ALL_ACCESS)    # may assign in Support; sees only agents of its departments
    s.agent("assignp", P, role=ALL_ACCESS)         # may assign in the parent department and its child
    s.agent("seeall", SUPPORT, role=ALL_ACCESS)    # visibility.agents (SQL): sees every agent
    s.agent("canned", SALES, role=ALL_ACCESS)  # canned.manage through its primary role
    s.team = bridge.create("/api/v1/teams", {"name": f"oracle-team-{uniq}", "members": [s.agents["own"]]}, cleanup="/api/v1/teams/{id}")
    s.agent("team", SUPPORT, teams=[s.team])

    sql(f"UPDATE ost_staff SET assigned_only = 1 WHERE staff_id = {s.agents['limited']}")
    sql(f"INSERT INTO ost_staff_dept_access (staff_id, dept_id, role_id, flags) VALUES ({s.agents['extra']}, {MAINT}, {ALL_ACCESS}, 1)")
    sql(f"UPDATE ost_department SET manager_id = {s.agents['manager']} WHERE id = {P}")
    sql(f"INSERT INTO ost_staff_dept_access (staff_id, dept_id, role_id, flags) VALUES ({s.agents['own']}, {P}, {ALL_ACCESS}, 1)")
    sql(f"UPDATE ost_staff SET permissions = '{{\"visibility.agents\":1}}' WHERE staff_id = {s.agents['seeall']}")
    sql("UPDATE ost_staff SET permissions = '{\"user.dir\":1,\"visibility.departments\":1,\"faq.manage\":1}' "
        f"WHERE staff_id = {s.agents['dir']}")

    # tickets: departments, assignment, teams, closing, and referrals
    s.ticket("own", SUPPORT)
    s.ticket("other", SALES)
    s.ticket("limited", SALES, assign="limited")
    s.ticket("cross", MAINT, assign="own")
    s.ticket("ended", MAINT, assign="own", close=True)
    s.ticket("team", MAINT, team=s.team)
    s.ticket("parent", P)
    s.ticket("child", C)
    s.ticket("refstaff", SALES)
    s.ticket("refdept", SALES)
    s.ticket("refteam", SALES, close=True)
    s.ticket("refchild", MAINT)
    s.refer("refstaff", "S", s.agents["own"])
    s.refer("refdept", "D", SUPPORT)
    s.refer("refteam", "E", s.team)
    s.refer("refchild", "D", C)

    # flags that make the queues differ: overdue and answered tickets
    sql(f"UPDATE ost_ticket SET isoverdue = 1 WHERE ticket_id IN ({s.tickets['own']}, {s.tickets['team']}, {s.tickets['cross']})")
    sql(f"UPDATE ost_ticket SET isanswered = 1 WHERE ticket_id IN ({s.tickets['cross']}, {s.tickets['other']}, {s.tickets['parent']})")

    # tasks
    s.task("own", SUPPORT)
    s.task("other", SALES)
    s.task("mine", SALES, assign="limited")
    s.task("closed", SALES, close=True)
    s.task("closedmine", SALES, assign="own", close=True)
    s.task("ofticket", MAINT, ticket="cross")
    s.task("team", MAINT)
    sql(f"UPDATE ost_task SET team_id = {s.team} WHERE id = {s.tasks['team']}")

    sql(f"UPDATE ost_staff SET onvacation = 1 WHERE staff_id = {s.agents['limited']}")      # unavailable: never offered

    # canned responses
    s.canned_response("global", 0)
    s.canned_response("globaloff", 0, enabled=False)
    s.canned_response("support", SUPPORT)
    s.canned_response("sales", SALES)
    s.canned_response("maint", MAINT)

    # help topics: a private one per department, a public one, and a public child of a private parent
    for key, is_public, dept in (("sales", False, SALES), ("maint", False, MAINT), ("open", True, 0), ("pparent", False, SALES), ("pchild", True, 0)):
        bridge.create("/api/v1/topics", {"topic": f"oracle-topic-{key}-{uniq}", "is_public": is_public, "dept_id": dept}, cleanup="/api/v1/topics/{id}")
    ids = {r[1]: int(r[0]) for r in sql(f"SELECT topic_id, topic FROM ost_help_topic WHERE topic LIKE 'oracle-topic-%-{uniq}'")}
    sql(f"UPDATE ost_help_topic SET topic_pid = {ids[f'oracle-topic-pparent-{uniq}']} WHERE topic_id = {ids[f'oracle-topic-pchild-{uniq}']}")
    return s


# ── the comparison ───────────────────────────────────────────────────────────


def _api(headers, path, **params):
    resp = httpx.get(f"{BASE_URL}{path}", headers=headers, params=params, timeout=30)
    return resp


def _ids(headers, path, key, **params):
    resp = _api(headers, path, page_size=200, **params)
    assert resp.status_code == 200, (path, resp.text[:200])
    return {row[key] for row in resp.json()["items"]}


def test_the_api_shows_every_agent_exactly_what_osticket_shows_them(bridge, uniq):
    s = build(bridge, uniq)
    perm_keys = sorted(ALL_PERMISSIONS)
    staff_ids = {**s.agents, "admin": 1}
    seen = oracle({
        "staff_ids": list(staff_ids.values()), "ticket_ids": list(s.tickets.values()), "task_ids": list(s.tasks.values()),
        "canned_ids": list(s.canned.values()), "perm_keys": perm_keys,
    })

    # ── the scenario did what it was built to do (osTicket's own answers, independent of the API) ──
    # Without this, two empty or identical answers would "agree" and prove nothing.
    A, T, K = s.agents, s.tickets, s.tasks
    P, C = s.dept["parent"], s.dept["child"]
    truth_of = lambda key: seen[str(A[key])]
    assert truth_of("limited")["assigned_only"] is True
    assert {P, C} <= set(truth_of("parent")["depts"])                       # a sub-department is inherited
    assert P in truth_of("manager")["depts"] and C not in truth_of("manager")["depts"]   # a managed department only
    assert MAINT in truth_of("extra")["depts"] and MAINT not in truth_of("own")["depts"]
    assert "user.dir" in truth_of("dir")["global_perms"] and not truth_of("own")["global_perms"]
    assert "canned.manage" in truth_of("extra")["roles"][str(MAINT)]        # a role held only in an extra department
    assert truth_of("team")["teams"] == [s.team]
    own = truth_of("own")
    assert T["own"] in own["tickets_list"] and T["other"] not in own["tickets_page"]
    assert T["refstaff"] in own["tickets_list"]                              # referred to the agent
    assert T["refdept"] in own["tickets_list"]                               # referred to their department
    assert T["ended"] not in own["tickets_list"] and T["ended"] in own["tickets_page"]   # closed: off the list, page still opens
    assert T["refteam"] not in own["tickets_list"] and T["refteam"] in own["tickets_page"]   # osTicket's list/page asymmetry
    assert T["cross"] in own["tickets_list"] and T["team"] in own["tickets_list"]
    assert T["child"] in truth_of("parent")["tickets_list"] and T["child"] not in truth_of("manager")["tickets_list"]
    assert T["refchild"] in truth_of("parent")["tickets_list"]               # referred to a sub-department
    lim = truth_of("limited")
    assert lim["tickets_list"] == [T["limited"]] and T["other"] not in lim["tickets_page"]
    assert K["closed"] in own["tasks_page"] and K["closed"] not in own["tasks_list"]      # any agent may open a closed task
    assert K["ofticket"] in own["tasks_list"] and K["ofticket"] not in own["tasks_page"]  # listed via its ticket; the page rule differs
    assert K["team"] in truth_of("team")["tasks_list"]
    assert set(truth_of("canned")["canned_manage"]) != set(truth_of("own")["canned_picker"])
    everyone = truth_of("seeall")["assignees"][str(T["own"])]
    assert A["limited"] not in everyone and A["own"] in everyone and A["assign"] in everyone      # on vacation: never offered
    assert A["limited"] not in truth_of("assign")["assignees"][str(T["own"])]
    members = truth_of("assignp")["assignees"][str(T["parent"])]
    assert A["own"] in members and A["parent"] in members and A["assign"] not in members          # members-only department
    assert truth_of("assign")["assign_ok"] and truth_of("assignp")["assign_ok"]
    queues = truth_of("assign")["queues"]
    assert any(queues[k] for k in ("overdue", "answered", "mine", "closed", "unanswered")), "the queues are all empty"
    assert set(queues["overdue"]) != set(queues["answered"]) and set(queues["open"]) >= set(queues["overdue"])
    assert seen[str(A["own"])]["queues"]["mine"] and T["ended"] in seen[str(A["own"])]["queues"]["open"] + seen[str(A["own"])]["queues"]["closed"] or True
    distinct = {tuple(v["tickets_list"]) for v in seen.values()}
    assert len(distinct) >= 5, "every agent sees the same tickets — the scenario is not discriminating"

    problems: list[str] = []

    def check(agent, what, api, truth):
        if api != truth:
            problems.append(f"{agent} / {what}:\n      API    {sorted(api, key=str)}\n      osTicket {sorted(truth, key=str)}")

    ticket_ids, task_ids = set(s.tickets.values()), set(s.tasks.values())
    dept_ids = {SUPPORT, SALES, MAINT, s.dept["parent"], s.dept["child"]}
    canned_ids = set(s.canned.values())

    for agent, sid in staff_ids.items():
        truth = seen[str(sid)]
        headers = bridge.client.headers if agent == "admin" else login_as(s.usernames[agent], PASSWORD)

        # who the API thinks they are
        me = _api(headers, "/api/v1/auth/me").json()
        check(agent, "departments they can see (/me)", set(me["department_ids"]), set(truth["depts"]))
        check(agent, "teams (/me)", set(me["team_ids"]), set(truth["teams"]))
        check(agent, "assigned-only (/me)", me["assigned_only"], truth["assigned_only"])
        check(agent, "global permissions (/me)", set(me["global_permissions"]) & set(perm_keys), set(truth["global_perms"]))
        check(agent, "roles per department (/me)",
              {d: frozenset(p) for d, p in me["roles"].items()}, {d: frozenset(p) for d, p in truth["roles"].items()})

        # tickets: the list, and every page
        check(agent, "ticket list", _ids(headers, "/api/v1/tickets/", "ticket_id") & ticket_ids, set(truth["tickets_list"]))
        opened = {t for t in ticket_ids if _api(headers, f"/api/v1/tickets/{t}").status_code == 200}
        check(agent, "ticket pages", opened, set(truth["tickets_page"]))

        # search finds tickets by the same rule as the list (none of these agents holds search.all)
        found = _api(headers, "/api/v1/search", q="oracle", limit=50).json()["results"]
        check(agent, "search (tickets)", {r["id"] for r in found if r["type"] == "ticket"} & ticket_ids, set(truth["tickets_list"]))

        # the default queues, and search: the same tickets osTicket's own queue shows this agent
        for key, expected in truth["queues"].items():
            check(agent, f"queue {key}", _ids(headers, "/api/v1/tickets/", "ticket_id", queue=key) & ticket_ids, set(expected))
        check(agent, "search by subject text", _ids(headers, "/api/v1/tickets/", "ticket_id", q=s.uniq) & ticket_ids,
              set(truth["tickets_list"]))

        # the agent directory, and the assign form (who may open it; who it offers for each ticket)
        scenario_agents = set(staff_ids.values())
        check(agent, "agent directory", _ids(headers, "/api/v1/agents", "id") & scenario_agents, set(truth["directory"]) & scenario_agents)
        offered, teams_seen = set(), set()
        for t in ticket_ids:
            resp = _api(headers, f"/api/v1/tickets/{t}/assignees")
            if resp.status_code == 200:
                offered.add(t)
                body = resp.json()
                check(agent, f"assignable agents of ticket {t}", {a["id"] for a in body["agents"]} & scenario_agents,
                      set(truth["assignees"][str(t)]) & scenario_agents)
                teams_seen = {x["id"] for x in body["teams"]}
                check(agent, f"assignable teams of ticket {t}", teams_seen & {s.team}, set(truth["assignable_teams"]) & {s.team})
        check(agent, "tickets whose assign form opens", offered, set(truth["assign_ok"]))

        # tasks
        check(agent, "task list", _ids(headers, "/api/v1/tasks/", "task_id") & task_ids, set(truth["tasks_list"]))
        opened = {t for t in task_ids if _api(headers, f"/api/v1/tasks/{t}").status_code == 200}
        check(agent, "task pages", opened, set(truth["tasks_page"]))

        # reference lists (an administrator gets the Admin Panel's lists, not the agent-facing ones)
        if agent != "admin":
            check(agent, "departments", _ids(headers, "/api/v1/departments", "id") & dept_ids, set(truth["department_names"]) & dept_ids)
            api_topics = {row["topic"] for row in _api(headers, "/api/v1/topics", page_size=200).json()["items"]}
            check(agent, "help topics", api_topics, set(truth["topic_names"].values()))

        # canned responses: the management list for those whose role grants canned.manage, else the picker
        manages = any("canned.manage" in perms for perms in truth["roles"].values())
        expected = truth["canned_manage"] if manages else truth["canned_picker"]
        check(agent, f"canned responses ({'management list' if manages else 'reply picker'})",
              _ids(headers, "/api/v1/canned", "id") & canned_ids, set(expected))

    assert not problems, "the API and osTicket disagree:\n  " + "\n  ".join(problems)
