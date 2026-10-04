"""Ticket and task lists: search, sort, queues and the real total."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.integration.osticket import read_repository as repo
from app.main import app
from tests.conftest import make_access
from tests.integration.test_staff import _auth

TICKET = {"ticket_id": 9, "number": "1", "subject": "s", "status_id": 1, "status_name": "Open", "status_state": "open",
          "dept_id": 1, "dept_name": "Support", "created": "2024-01-01T00:00:00", "updated": "2024-01-01T00:00:00"}


async def _get(path, headers=None):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        return await ac.get(path, headers=headers or {})


@pytest.mark.asyncio
async def test_ticket_list_passes_search_queue_and_sort_and_reports_the_total():
    listing, counting = AsyncMock(return_value=[TICKET]), AsyncMock(return_value=137)
    with patch("app.modules.tickets.service.repo.list_tickets", new=listing), \
            patch("app.modules.tickets.service.repo.count_tickets", new=counting):
        resp = await _get("/api/v1/tickets/?q=printer&queue=overdue&sort=due_date&descending=false&page=2&page_size=10", _auth())
    assert resp.status_code == 200 and resp.json()["total"] == 137 and resp.json()["page"] == 2
    kwargs = listing.call_args.kwargs
    assert (kwargs["q"], kwargs["queue"], kwargs["sort"], kwargs["descending"], kwargs["limit"], kwargs["offset"]) == \
        ("printer", "overdue", "due_date", False, 10, 10)
    assert counting.call_args.kwargs["q"] == "printer" and "limit" not in counting.call_args.kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["queue=everything", "sort=password", "queue=open;drop"])
async def test_an_unknown_queue_or_sort_is_a_422_not_sql(query):
    assert (await _get(f"/api/v1/tickets/?{query}", _auth())).status_code == 422


@pytest.mark.asyncio
async def test_an_unknown_task_sort_is_a_422():
    assert (await _get("/api/v1/tasks/?sort=password", _auth())).status_code == 422


@pytest.mark.asyncio
async def test_queue_counts_come_in_osticket_order_with_parents():
    counts = {"open": 5, "unanswered": 3, "answered": 2, "overdue": 1, "mine": 4, "assigned_me": 3, "assigned_teams": 1, "closed": 9}
    with patch("app.modules.tickets.service.repo.count_ticket_queues", new=AsyncMock(return_value=counts)) as counting:
        resp = await _get("/api/v1/tickets/queues", _auth())
    assert resp.status_code == 200
    queues = resp.json()["queues"]
    assert [q["key"] for q in queues] == ["open", "unanswered", "answered", "overdue", "mine", "assigned_me", "assigned_teams", "closed"]
    assert [q["parent"] for q in queues] == [None, "open", "open", "open", None, "mine", "mine", None]
    assert [q["count"] for q in queues] == [5, 3, 2, 1, 4, 3, 1, 9] and queues[1]["title"] == "Open"
    assert counting.call_args.kwargs["access"].staff_id == 5
    assert (await _get("/api/v1/tickets/queues")).status_code == 401


def test_the_queue_conditions_are_osticks_default_queues():
    me = make_access(staff_id=7)
    with_team = type("A", (), {"staff_id": 7, "team_ids": frozenset({3, 4})})()
    cond = lambda key, a=with_team: repo._queue_condition(key, a)
    assert cond("open") == "ts.state = 'open'" and cond("closed") == "ts.state = 'closed'"
    assert "t.isanswered = 0" in cond("unanswered") and "t.isanswered = 1" in cond("answered")
    assert "t.isoverdue = 1" in cond("overdue")
    assert "t.staff_id = 7" in cond("mine") and "t.team_id IN (3, 4)" in cond("mine").replace(",3,4", ", 3, 4").replace("(3,4)", "(3, 4)")
    assert "t.staff_id = 7" in cond("assigned_me") and "t.staff_id <> 7" in cond("assigned_teams")
    assert cond("assigned_teams", me).endswith("1 = 0")          # no teams: nothing is "assigned to my teams"
    with pytest.raises(KeyError):
        cond("anything else")
