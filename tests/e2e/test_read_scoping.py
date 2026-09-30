"""S3 against a real osTicket: an agent sees what osTicket shows them, and nothing else.

Two agents in different departments, a team, and tickets/tasks in every state that matters. Every read is
checked as each agent and as the admin. What cannot be set up through the API (limited-access agents, referrals)
is covered by the SQL-semantics tests in tests/unit/test_visibility_sql.py.
"""

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
        assert got.status_code == 200 and got.json()["thread"], visible
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
    assert _get(agent_a, f"/api/v1/tasks/{other}").status_code == 404
    assert _get(agent_a, f"/api/v1/tasks/{own}").status_code == 200
    got = _get(agent_a, f"/api/v1/tasks/{mine}")
    assert got.status_code == 200 and got.json()["thread"]
    # ...but Task::checkStaffPerm lets any agent open a CLOSED task, and its assignee open it in any state
    assert _get(agent_a, f"/api/v1/tasks/{closed_mine}").status_code == 200
    assert _get(agent_a, f"/api/v1/tasks/{closed_other}").status_code == 200
