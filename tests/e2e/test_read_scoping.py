"""S3 against a real osTicket: an agent sees what osTicket shows them, and nothing else.

Two agents in different departments, a team, and tickets/tasks in every state that matters. Every read is
checked as each agent and as the admin. What cannot be set up through the API (limited-access agents, referrals)
is covered by the SQL-semantics tests in tests/unit/test_visibility_sql.py.
"""

import json

import httpx
import pytest

from tests.e2e.conftest import BASE_URL, DELIVERABLE_DOMAIN, login_as

pytestmark = pytest.mark.e2e

PASSWORD = "E2e-Pass-9x!Qz"
SUPPORT, SALES = 1, 2      # the seeded departments (Support, Sales)


def _get(headers, path, **params):
    return httpx.get(f"{BASE_URL}{path}", headers=headers, params=params, timeout=30)


def _ids(headers, path, key="task_id", **params):
    resp = _get(headers, path, page_size=200, **params)
    assert resp.status_code == 200, resp.text
    return {row[key] for row in resp.json()["items"]}


def _agent(bridge, uniq, who, dept):
    username = f"e2e{who}{uniq}"
    sid = bridge.create(
        "/api/v1/staff",
        {"username": username, "firstname": "E2E", "lastname": who.title(), "email": f"{username}@{DELIVERABLE_DOMAIN}",
         "dept_id": dept, "role_id": 3, "password": PASSWORD, "is_active": True, "is_admin": False},
        cleanup="/api/v1/staff/{id}",
    )
    return sid, username


def _ticket(bridge, uniq, suffix, dept):
    """A ticket for a fresh customer, moved to ``dept``; cleanup deletes the ticket, then the customer."""
    tk = bridge.create(
        "/api/v1/tickets/",
        {"subject": f"e2e scope {uniq} {suffix}", "message": "hello there",
         "email": f"scope.{suffix}.{uniq}@{DELIVERABLE_DOMAIN}", "name": "E2E Customer", "dept_id": SUPPORT, "topic_id": 1},
    )
    user_id = bridge.read(f"/api/v1/tickets/{tk}")["user_id"]
    bridge._cleanups.append(("DELETE", f"/api/v1/users/{user_id}"))
    bridge._cleanups.append(("DELETE", f"/api/v1/tickets/{tk}"))
    if dept != SUPPORT:
        bridge.command("POST", f"/api/v1/tickets/{tk}/transfer", {"dept_id": dept, "note": "scope test"})
    return tk


def test_tickets_are_visible_by_department_assignment_and_team(bridge, uniq):
    a_id, a_user = _agent(bridge, uniq, "a", SUPPORT)      # Support
    b_id, b_user = _agent(bridge, uniq, "b", SALES)        # Sales
    team = bridge.create("/api/v1/teams", {"name": f"e2e-scope-{uniq}", "members": [a_id]}, cleanup="/api/v1/teams/{id}")

    own = _ticket(bridge, uniq, "own", SUPPORT)                       # A's department
    other = _ticket(bridge, uniq, "other", SALES)                     # Sales only
    mine = _ticket(bridge, uniq, "mine", SALES)                       # Sales, assigned to A
    teams = _ticket(bridge, uniq, "team", SALES)                      # Sales, assigned to A's team
    gone = _ticket(bridge, uniq, "gone", SALES)                       # Sales, assigned to A, then closed
    bridge.command("POST", f"/api/v1/tickets/{mine}/assign", {"staff_id": a_id, "note": "yours"})
    bridge.command("POST", f"/api/v1/tickets/{teams}/assign-team", {"team_id": team, "note": "team"})
    bridge.command("POST", f"/api/v1/tickets/{gone}/assign", {"staff_id": a_id, "note": "yours"})
    bridge.command("POST", f"/api/v1/tickets/{gone}/close", {"note": "done"})

    admin, agent_a, agent_b = bridge.client.headers, login_as(a_user, PASSWORD), login_as(b_user, PASSWORD)

    # A (Support): own department, plus what is assigned to them or their team while open
    seen_a = _ids(agent_a, "/api/v1/tickets/", key="ticket_id")
    assert {own, mine, teams} <= seen_a
    assert other not in seen_a and gone not in seen_a          # someone else's; and an assignment that ended with the close

    # B (Sales): all of Sales, nothing of Support
    seen_b = _ids(agent_b, "/api/v1/tickets/", key="ticket_id")
    assert {other, mine, teams, gone} <= seen_b and own not in seen_b

    # the admin (Support + Sales + Maintenance) sees everything
    assert {own, other, mine, teams, gone} <= _ids(admin, "/api/v1/tickets/", key="ticket_id")

    # a filter cannot widen it: asking for Sales tickets still returns only A's visible ones
    only_sales = _ids(agent_a, "/api/v1/tickets/", key="ticket_id", dept_id=SALES)
    assert mine in only_sales and other not in only_sales and own not in only_sales

    # the page: 200 with its thread when visible; 404 (existence not disclosed) when not
    for visible in (own, mine, teams):
        got = _get(agent_a, f"/api/v1/tickets/{visible}")
        assert got.status_code == 200 and got.json()["thread"], f"{visible}: {got.status_code} {got.text[:200]}"
    assert _get(agent_a, f"/api/v1/tickets/{other}").status_code == 404
    # `gone` is off A's list and search (an assignment stops counting once the ticket closes), but osTicket
    # refers a closing ticket's thread to its previously assigned agent (autoReferTicketsOnClose), and
    # Ticket::checkStaffPerm lets a referred agent open it in any state — so the page still opens.
    assert _get(agent_a, f"/api/v1/tickets/{gone}").status_code == 200
    assert _get(agent_b, f"/api/v1/tickets/{own}").status_code == 404
    assert _get(agent_b, f"/api/v1/tickets/{other}").status_code == 200

    # search finds only what the agent may open (this agent has no user.dir, so no people either)
    found = {r["id"] for r in _get(agent_a, "/api/v1/search", q=f"e2e scope {uniq}").json()["results"]}
    assert {own, mine, teams} <= found and other not in found and gone not in found
    assert {r["type"] for r in _get(agent_a, "/api/v1/search", q=f"e2e scope {uniq}").json()["results"]} == {"ticket"}
    assert other in {r["id"] for r in _get(agent_b, "/api/v1/search", q=f"e2e scope {uniq}").json()["results"]}


def test_tasks_follow_osticket_including_its_closed_task_quirk(bridge, uniq):
    a_id, a_user = _agent(bridge, uniq, "ta", SUPPORT)
    def task(suffix, dept=SALES):
        return bridge.create("/api/v1/tasks/", {"title": f"e2e scope task {uniq} {suffix}", "description": "x", "dept_id": dept},
                             cleanup="/api/v1/tasks/{id}")

    own, other, mine, closed_mine, closed_other = (task("own", SUPPORT), task("other"), task("mine"), task("cm"), task("co"))
    bridge.command("POST", f"/api/v1/tasks/{mine}/assign", {"staff_id": a_id, "note": "yours"})
    bridge.command("POST", f"/api/v1/tasks/{closed_mine}/assign", {"staff_id": a_id, "note": "yours"})
    bridge.command("POST", f"/api/v1/tasks/{closed_mine}/close", {})
    bridge.command("POST", f"/api/v1/tasks/{closed_other}/close", {})

    agent_a = login_as(a_user, PASSWORD)
    listed = _ids(agent_a, "/api/v1/tasks/")
    assert {own, mine} <= listed                                   # my department; open and assigned to me
    assert other not in listed                                     # open, elsewhere, not mine
    assert closed_mine not in listed and closed_other not in listed   # the list shows my open work and my departments

    # the page: 404 for an open task that is not theirs...
    _r = _get(agent_a, f"/api/v1/tasks/{other}")
    assert _r.status_code == 404, f"{_r.status_code} {_r.text[:200]}"
    _r = _get(agent_a, f"/api/v1/tasks/{own}")
    assert _r.status_code == 200, f"{_r.status_code} {_r.text[:200]}"
    got = _get(agent_a, f"/api/v1/tasks/{mine}")
    assert got.status_code == 200 and got.json()["thread"], f"{got.status_code} {got.text[:200]}"
    # ...but Task::checkStaffPerm lets any agent open a CLOSED task, and its assignee open it in any state
    _r = _get(agent_a, f"/api/v1/tasks/{closed_mine}")
    assert _r.status_code == 200, f"{_r.status_code} {_r.text[:200]}"
    _r = _get(agent_a, f"/api/v1/tasks/{closed_other}")
    assert _r.status_code == 200, f"{_r.status_code} {_r.text[:200]}"


def _titles(headers, path, key="title"):
    resp = _get(headers, path, page_size=200)
    assert resp.status_code == 200, resp.text
    return {row[key] for row in resp.json()["items"]}


def test_reference_lists_follow_what_osticket_offers_an_agent(bridge, uniq):
    a_id, a_user = _agent(bridge, uniq, "ra", SUPPORT)                      # Support, Limited Access role
    b_id, b_user = _agent(bridge, uniq, "rb", SALES)                        # Sales
    manager = f"e2erc{uniq}"
    bridge.create(                                                          # Support, All Access role (canned.manage)
        "/api/v1/staff",
        {"username": manager, "firstname": "E2E", "lastname": "Rc", "email": f"{manager}@{DELIVERABLE_DOMAIN}",
         "dept_id": SUPPORT, "role_id": 1, "password": PASSWORD, "is_active": True, "is_admin": False},
        cleanup="/api/v1/staff/{id}",
    )
    maintenance = 3
    topics = {
        "sales": f"e2e-private-sales-{uniq}", "mnt": f"e2e-private-maint-{uniq}", "open": f"e2e-open-{uniq}",
    }
    bridge.create("/api/v1/topics", {"topic": topics["sales"], "is_public": False, "dept_id": SALES}, cleanup="/api/v1/topics/{id}")
    bridge.create("/api/v1/topics", {"topic": topics["mnt"], "is_public": False, "dept_id": maintenance}, cleanup="/api/v1/topics/{id}")
    bridge.create("/api/v1/topics", {"topic": topics["open"], "is_public": True, "dept_id": 0}, cleanup="/api/v1/topics/{id}")

    canned = {}
    for label, dept, enabled in (("glob", 0, True), ("globoff", 0, False), ("sup", SUPPORT, True), ("sales", SALES, True)):
        canned[label] = f"e2e-canned-{label}-{uniq}"
        bridge.create("/api/v1/canned", {"title": canned[label], "response": "text", "dept_id": dept, "is_enabled": enabled},
                      cleanup="/api/v1/canned/{id}")

    admin = bridge.client.headers
    agent_a, agent_b, agent_c = login_as(a_user, PASSWORD), login_as(b_user, PASSWORD), login_as(manager, PASSWORD)

    # departments: an agent sees their own, the administrator sees all
    assert _ids(agent_a, "/api/v1/departments", key="id") == {SUPPORT}
    assert _ids(agent_b, "/api/v1/departments", key="id") == {SALES}
    assert {SUPPORT, SALES, maintenance} <= _ids(admin, "/api/v1/departments", key="id")
    assert _get(agent_a, f"/api/v1/departments/{SUPPORT}").status_code == 403       # the detail stays admin-only

    # help topics: public ones for everyone; a private topic only inside its department
    seen_a, seen_b = _titles(agent_a, "/api/v1/topics", "topic"), _titles(agent_b, "/api/v1/topics", "topic")
    assert topics["open"] in seen_a and topics["open"] in seen_b
    assert topics["sales"] not in seen_a and topics["mnt"] not in seen_a
    assert topics["sales"] in seen_b and topics["mnt"] not in seen_b
    assert "Report a Problem / Access Issue" in seen_a                              # osTicket's "Parent / Child" names
    listed = [row["topic"] for row in _get(agent_a, "/api/v1/topics", page_size=200).json()["items"]]
    assert listed == sorted(listed, key=str.casefold)                               # alphabetical, osTicket's default
    assert {topics["sales"], topics["mnt"], topics["open"]} <= _titles(admin, "/api/v1/topics", "topic")   # the Admin Panel sees all

    # priorities: the list is for every agent, the detail is not
    assert _ids(agent_a, "/api/v1/priorities", key="id")
    assert _get(agent_a, "/api/v1/priorities/1").status_code == 403

    # canned responses: the reply picker (enabled; global or my department) vs the management list
    picker = _titles(agent_a, "/api/v1/canned") & set(canned.values())
    assert picker == {canned["glob"], canned["sup"]}
    managed = _titles(agent_c, "/api/v1/canned") & set(canned.values())
    assert managed == {canned["glob"], canned["globoff"], canned["sup"]}            # disabled ones too; not Sales'
    everything = _titles(admin, "/api/v1/canned") & set(canned.values())
    assert everything == set(canned.values())                                       # the admin holds canned.manage in all three


def test_a_command_and_its_idempotency_key_belong_to_the_agent_who_queued_it(bridge, uniq):
    a_id, a_user = _agent(bridge, uniq, "ca", SUPPORT)
    b_id, b_user = _agent(bridge, uniq, "cb", SUPPORT)
    admin, agent_a, agent_b = bridge.client.headers, login_as(a_user, PASSWORD), login_as(b_user, PASSWORD)
    shared_key = f"e2e-shared-key-{uniq}"
    # osTicket lets an agent open a ticket only for an existing customer unless they hold user.create,
    # so the administrator creates the two customers first (registered first, so removed last)
    for who in ("a", "b"):
        bridge.create("/api/v1/users", {"email": f"cmd.{who}.{uniq}@{DELIVERABLE_DOMAIN}", "name": f"E2E Customer {who}"},
                      cleanup="/api/v1/users/{id}")

    def queue(headers, who):
        resp = httpx.post(
            f"{BASE_URL}/api/v1/tickets/", headers={**headers, "Idempotency-Key": shared_key}, timeout=30,
            json={"subject": f"e2e cmd {uniq} {who}", "message": "hello", "email": f"cmd.{who}.{uniq}@{DELIVERABLE_DOMAIN}",
                  "name": "E2E Customer", "dept_id": SUPPORT, "topic_id": 1},
        )
        assert resp.status_code == 202, resp.text
        return resp.json()["command_id"]

    def finish(headers, command_id):
        resp = httpx.get(f"{BASE_URL}/api/v1/commands/{command_id}", headers=headers, params={"wait": 25}, timeout=40)
        assert resp.status_code == 200, resp.text
        return resp.json()

    cmd_a, cmd_b = queue(agent_a, "a"), queue(agent_b, "b")
    assert cmd_a != cmd_b                                              # the same key from two agents: two commands
    assert queue(agent_a, "a") == cmd_a                                # ...and from one agent, the same command

    done_a, done_b = finish(agent_a, cmd_a), finish(agent_b, cmd_b)
    assert done_a["status"] == done_b["status"] == "done"
    assert done_a["idempotency_key"] == shared_key                     # the client's own key, not the internal prefix
    for done in (done_a, done_b):                                      # tidy up what the agents created
        ticket_id = json.loads(done["result_payload"])["ticket_id"]
        bridge._cleanups.append(("DELETE", f"/api/v1/tickets/{ticket_id}"))

    # a command is its owner's: another agent gets a 404, an administrator sees it
    assert httpx.get(f"{BASE_URL}/api/v1/commands/{cmd_a}", headers=agent_b, timeout=30).status_code == 404
    assert httpx.get(f"{BASE_URL}/api/v1/commands/{cmd_a}", headers=admin, timeout=30).status_code == 200
    listed_a = {row["id"] for row in _get(agent_a, "/api/v1/commands", page_size=200).json()["items"]}
    assert cmd_a in listed_a and cmd_b not in listed_a
    listed_admin = {row["id"] for row in _get(admin, "/api/v1/commands", page_size=200).json()["items"]}
    assert {cmd_a, cmd_b} <= listed_admin
