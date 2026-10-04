"""Integration tests for ticket endpoints (Phase 2).

Strategy:
  - Write endpoints: use auth_user fixture + verify inbox row via SQLite.
    No mocking needed — dispatch_command writes to our integration tables.
  - Read endpoints: mock repository functions (they query osTicket tables
    that don't exist in the test DB).

All tests require the setup_db autouse fixture from conftest.py (wipes
and recreates tables between tests).
"""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import select

from app.integration.osticket.commands import IntegrationInbox
from app.main import app
from tests.conftest import TestingSessionLocal

TICKETS_URL = "/api/v1/tickets"

# ── Shared test data ───────────────────────────────────────────────────────────

_NOW = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)

MOCK_TICKET = {
    "ticket_id": 1,
    "number": "123456",
    "subject": "Printer not working",
    "status_id": 1,
    "status_name": "Open",
    "status_state": "open",
    "dept_id": 2,
    "dept_name": "Support",
    "assignee_staff_id": None,
    "assignee_name": None,
    "team_id": None,
    "team_name": None,
    "user_id": 10,
    "user_name": "Alice User",
    "topic_id": 3,
    "topic_name": "Hardware",
    "sla_id": 1,
    "sla_name": "Standard",
    "source": "web",
    "is_answered": 0,
    "created": _NOW,
    "updated": _NOW,
    "closed": None,
    "due_date": None,
}

MOCK_THREAD_ENTRY = {
    "id": 1,
    "type": "M",
    "title": "Printer not working",
    "body": "My printer stopped working this morning.",
    "staff_id": None,
    "user_id": 10,
    "poster": "Alice User",
    "ip_address": "192.168.1.1",
    "created": _NOW,
}

MOCK_COLLABORATOR = {
    "id": 1,
    "user_id": 11,
    "name": "Bob Watcher",
    "email": "bob@example.local",
}


# ── Token helper ───────────────────────────────────────────────────────────────

def _token(permissions: list[str], is_admin: bool = False) -> str:
    from datetime import timedelta
    from app.core.auth import create_access_token
    return create_access_token(
        {
            "staff_id": 5,
            "username": "agent",
            "name": "Test Agent",
            "email": "agent@test.local",
            "is_admin": is_admin,
            "permissions": permissions,
        },
        expires_delta=timedelta(minutes=5),
    )


def _auth(permissions: list[str] | None = None, is_admin: bool = False) -> dict:
    return {"Authorization": f"Bearer {_token(permissions or [], is_admin)}"}


def _admin_auth() -> dict:
    return _auth(is_admin=True)


async def _inbox_rows() -> list[IntegrationInbox]:
    async with TestingSessionLocal() as s:
        result = await s.execute(select(IntegrationInbox))
        return result.scalars().all()


# ─────────────────────────────────────────────────────────────────────────────
# READ TESTS
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_tickets_returns_200():
    """GET /tickets/ returns 200 with items list."""
    with (
        patch("app.modules.tickets.service.repo.list_tickets", new=AsyncMock(return_value=[MOCK_TICKET])),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(TICKETS_URL + "/", headers=_auth(["ticket.create"]))
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert len(data["items"]) == 1
    assert data["items"][0]["ticket_id"] == 1
    assert data["items"][0]["number"] == "123456"


@pytest.mark.asyncio
async def test_list_tickets_requires_auth():
    """GET /tickets/ returns 401 without a token."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(TICKETS_URL + "/")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_listing_tickets_needs_no_permission_only_an_agent():
    """osTicket gates the ticket list on visibility, not on a permission: any enabled agent may ask."""
    with patch("app.modules.tickets.service.repo.list_tickets", new=AsyncMock(return_value=[])):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(TICKETS_URL + "/", headers=_auth([]))
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_the_callers_access_is_what_scopes_the_ticket_reads():
    """The repository receives the caller's AccessContext — that is what narrows every query,
    the thread and the collaborators included."""
    lst, one = AsyncMock(return_value=[]), AsyncMock(return_value=None)
    with patch("app.modules.tickets.service.repo.list_tickets", new=lst), \
         patch("app.modules.tickets.service.repo.get_ticket", new=one):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            await ac.get(TICKETS_URL + "/", headers=_auth([]))
            resp = await ac.get(TICKETS_URL + "/9", headers=_auth([]))
    assert lst.await_args.kwargs["access"].staff_id == 5
    assert one.await_args.kwargs["access"].staff_id == 5
    assert resp.status_code == 404          # a ticket the agent may not open looks like one that is not there


@pytest.mark.asyncio
async def test_thread_and_collaborators_are_read_with_the_same_access():
    row = {"ticket_id": 9, "number": "1", "subject": "s", "status_id": 1, "status_name": "Open",
           "status_state": "open", "dept_id": 1, "dept_name": "Support", "created": "2024-01-01T00:00:00",
           "updated": "2024-01-01T00:00:00"}
    thread, collab, customs = AsyncMock(return_value=[]), AsyncMock(return_value=[]), AsyncMock(return_value=[])
    with patch("app.modules.tickets.service.repo.get_ticket", new=AsyncMock(return_value=row)), \
         patch("app.modules.tickets.service.repo.get_ticket_thread", new=thread), \
         patch("app.modules.tickets.service.repo.get_ticket_attachments", new=AsyncMock(return_value=[])), \
         patch("app.modules.tickets.service.repo.get_ticket_collaborators", new=collab), \
         patch("app.modules.tickets.service.repo.get_ticket_custom_fields", new=customs):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            await ac.get(TICKETS_URL + "/9", headers=_auth([]))
    assert customs.await_args.kwargs["access"].staff_id == 5
    assert thread.await_args.kwargs["access"].staff_id == 5
    assert collab.await_args.kwargs["access"].staff_id == 5


@pytest.mark.asyncio
async def test_list_tickets_pagination_params():
    """Page and page_size query params are accepted."""
    with (
        patch("app.modules.tickets.service.repo.list_tickets", new=AsyncMock(return_value=[])),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(
                TICKETS_URL + "/", params={"page": 2, "page_size": 10},
                headers=_auth(["ticket.create"]),
            )
    assert resp.status_code == 200
    data = resp.json()
    assert data["page"] == 2
    assert data["page_size"] == 10


@pytest.mark.asyncio
async def test_list_tickets_page_size_cap():
    """page_size > 200 returns 422 (validation error)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(
            TICKETS_URL + "/", params={"page_size": 999},
            headers=_auth(["ticket.create"]),
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_ticket_returns_detail():
    """GET /tickets/{id} returns the full ticket with thread and collaborators."""
    with (
        patch("app.modules.tickets.service.repo.get_ticket", new=AsyncMock(return_value=MOCK_TICKET)),
        patch("app.modules.tickets.service.repo.get_ticket_thread", new=AsyncMock(return_value=[MOCK_THREAD_ENTRY])),
        patch("app.modules.tickets.service.repo.get_ticket_attachments", new=AsyncMock(return_value=[])),
        patch("app.modules.tickets.service.repo.get_ticket_collaborators", new=AsyncMock(return_value=[MOCK_COLLABORATOR])),
        patch("app.modules.tickets.service.repo.get_ticket_custom_fields", new=AsyncMock(return_value=[
            {"name": "asset", "label": "Asset", "type": "text", "value": "A-1", "value_id": None},
            {"name": "subject", "label": "Issue", "type": "text", "value": "dup of the top-level subject", "value_id": None}])),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(TICKETS_URL + "/1", headers=_auth(["ticket.create"]))
    assert resp.status_code == 200
    data = resp.json()
    assert data["ticket_id"] == 1
    assert data["subject"] == "Printer not working"
    assert len(data["thread"]) == 1
    assert data["thread"][0]["type"] == "M"
    assert len(data["collaborators"]) == 1
    assert data["collaborators"][0]["name"] == "Bob Watcher"
    assert [c["name"] for c in data["custom_fields"]] == ["asset"]       # the built-in subject is not repeated
    assert data["custom_fields"][0]["value"] == "A-1"


@pytest.mark.asyncio
async def test_get_ticket_not_found_returns_404():
    """GET /tickets/{id} returns 404 when ticket doesn't exist."""
    with patch("app.modules.tickets.service.repo.get_ticket", new=AsyncMock(return_value=None)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(TICKETS_URL + "/9999", headers=_auth(["ticket.create"]))
    assert resp.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# WRITE TESTS — command queuing
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_ticket_queues_command():
    """POST /tickets/ queues a ticket.create command and returns 202."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            TICKETS_URL + "/",
            json={"subject": "My keyboard broke", "message": "Keys stopped working."},
            headers=_auth(["ticket.create"]),
        )
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "pending"
    assert "command_id" in data

    rows = await _inbox_rows()
    assert len(rows) == 1
    assert rows[0].command_type == "ticket.create"
    payload = json.loads(rows[0].payload)
    assert payload["subject"] == "My keyboard broke"
    assert payload["message"] == "Keys stopped working."
    assert rows[0].requested_by_staff_id == 5


@pytest.mark.asyncio
async def test_create_ticket_stores_optional_fields():
    """Optional fields (dept_id, topic_id, priority_id) are included in payload."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            TICKETS_URL + "/",
            json={
                "subject": "Server down",
                "message": "Production server is unreachable.",
                "email": "user@example.com",
                "dept_id": 3,
                "topic_id": 7,
                "priority_id": 1,
            },
            headers=_auth(["ticket.create"]),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    payload = json.loads(rows[0].payload)
    assert payload["dept_id"] == 3
    assert payload["topic_id"] == 7
    assert payload["priority_id"] == 1
    assert payload["email"] == "user@example.com"


@pytest.mark.asyncio
async def test_create_ticket_requires_permission():
    """POST /tickets/ returns 403 without ticket.create permission."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            TICKETS_URL + "/",
            json={"subject": "Test", "message": "Body"},
            headers=_auth([]),
        )
    assert resp.status_code == 403
    rows = await _inbox_rows()
    assert len(rows) == 0  # nothing queued


@pytest.mark.asyncio
async def test_create_ticket_validates_empty_subject():
    """Empty subject returns 422."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            TICKETS_URL + "/",
            json={"subject": "", "message": "Body"},
            headers=_auth(["ticket.create"]),
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_ticket_idempotency_key_deduplicates():
    """Two requests with same Idempotency-Key create only one inbox row."""
    headers = {**_auth(["ticket.create"]), "Idempotency-Key": "create-key-abc"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r1 = await ac.post(TICKETS_URL + "/", json={"subject": "Test", "message": "Body"}, headers=headers)
        r2 = await ac.post(TICKETS_URL + "/", json={"subject": "Test", "message": "Body"}, headers=headers)

    assert r1.status_code == 202
    assert r2.status_code == 202
    assert r1.json()["command_id"] == r2.json()["command_id"]
    rows = await _inbox_rows()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_reply_queues_command():
    """POST /tickets/{id}/reply queues a ticket.reply command."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            TICKETS_URL + "/42/reply",
            json={"message": "We are looking into this."},
            headers=_auth(["ticket.reply"]),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "ticket.reply"
    assert rows[0].aggregate_id == "42"
    payload = json.loads(rows[0].payload)
    assert payload["ticket_id"] == 42
    assert payload["message"] == "We are looking into this."


@pytest.mark.asyncio
async def test_add_note_queues_command():
    """POST /tickets/{id}/note queues a ticket.note command."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            TICKETS_URL + "/7/note",
            json={"title": "Internal note", "message": "Called user, no answer."},
            headers=_auth(["ticket.reply"]),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "ticket.note"
    payload = json.loads(rows[0].payload)
    assert payload["title"] == "Internal note"


@pytest.mark.asyncio
async def test_close_ticket_queues_command():
    """POST /tickets/{id}/close queues a ticket.close command."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            TICKETS_URL + "/3/close",
            json={},
            headers=_auth(["ticket.close"]),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "ticket.close"
    assert rows[0].aggregate_id == "3"


@pytest.mark.asyncio
async def test_reopen_ticket_queues_command():
    """POST /tickets/{id}/reopen queues a ticket.reopen command."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            TICKETS_URL + "/3/reopen",
            json={},
            headers=_auth(["ticket.close"]),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "ticket.reopen"


@pytest.mark.asyncio
async def test_assign_ticket_queues_command():
    """POST /tickets/{id}/assign queues a ticket.assign command with staff_id."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            TICKETS_URL + "/10/assign",
            json={"staff_id": 3, "note": "Assigning to senior agent."},
            headers=_auth(["ticket.assign"]),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "ticket.assign"
    payload = json.loads(rows[0].payload)
    assert payload["staff_id"] == 3
    assert payload["note"] == "Assigning to senior agent."


@pytest.mark.asyncio
async def test_assign_team_queues_command():
    """POST /tickets/{id}/assign-team queues a ticket.assign_team command."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            TICKETS_URL + "/10/assign-team",
            json={"team_id": 2},
            headers=_auth(["ticket.assign"]),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "ticket.assign_team"
    payload = json.loads(rows[0].payload)
    assert payload["team_id"] == 2


@pytest.mark.asyncio
async def test_transfer_ticket_queues_command():
    """POST /tickets/{id}/transfer queues a ticket.transfer command."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            TICKETS_URL + "/5/transfer",
            json={"dept_id": 4, "note": "Moving to network team."},
            headers=_auth(["ticket.transfer"]),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "ticket.transfer"
    payload = json.loads(rows[0].payload)
    assert payload["dept_id"] == 4


@pytest.mark.asyncio
async def test_change_owner_queues_command():
    """POST /tickets/{id}/change-owner queues a ticket.change_owner command."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            TICKETS_URL + "/5/change-owner",
            json={"user_id": 20},
            headers=_auth(["ticket.edit"]),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "ticket.change_owner"
    payload = json.loads(rows[0].payload)
    assert payload["user_id"] == 20


@pytest.mark.asyncio
async def test_update_ticket_queues_only_the_given_fields():
    """PATCH /tickets/{id} queues ticket.update with just the fields sent (null due_date kept)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.patch(
            TICKETS_URL + "/5",
            json={"subject": "New subject", "priority_id": 3, "due_date": None},
            headers={**_auth(["ticket.edit"]), "Idempotency-Key": "upd-1"},
        )
        again = await ac.patch(
            TICKETS_URL + "/5",
            json={"subject": "New subject", "priority_id": 3, "due_date": None},
            headers={**_auth(["ticket.edit"]), "Idempotency-Key": "upd-1"},
        )
    assert resp.status_code == 202
    assert again.json()["command_id"] == resp.json()["command_id"]
    rows = await _inbox_rows()
    assert len(rows) == 1
    assert rows[0].command_type == "ticket.update"
    assert rows[0].aggregate_id == "5"
    assert json.loads(rows[0].payload) == {
        "ticket_id": 5, "subject": "New subject", "priority_id": 3, "due_date": None,
    }


@pytest.mark.asyncio
async def test_update_ticket_rejects_bad_input_and_missing_perm():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        bad = await ac.patch(TICKETS_URL + "/5", json={"subject": ""}, headers=_auth(["ticket.edit"]))
        denied = await ac.patch(TICKETS_URL + "/5", json={"subject": "x"}, headers=_auth(["ticket.reply"]))
    assert bad.status_code == 422
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_delete_ticket_queues_command():
    """DELETE /tickets/{id} queues a ticket.delete command."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.delete(
            TICKETS_URL + "/99",
            headers=_auth(["ticket.delete"]),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "ticket.delete"
    assert rows[0].aggregate_id == "99"


@pytest.mark.asyncio
async def test_merge_tickets_queues_command():
    """POST /tickets/{id}/merge queues a ticket.merge command with child_ids."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            TICKETS_URL + "/1/merge",
            json={"child_ids": [2, 3, 4]},
            headers=_auth(["ticket.merge"]),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "ticket.merge"
    payload = json.loads(rows[0].payload)
    assert payload["child_ids"] == [2, 3, 4]


@pytest.mark.asyncio
async def test_merge_requires_child_ids():
    """POST /tickets/{id}/merge with empty child_ids returns 422."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            TICKETS_URL + "/1/merge",
            json={"child_ids": []},
            headers=_auth(["ticket.merge"]),
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_mark_answered_queues_command():
    """POST /tickets/{id}/answered queues ticket.answered."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            TICKETS_URL + "/8/answered",
            headers=_auth(["ticket.markanswered"]),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "ticket.answered"


@pytest.mark.asyncio
async def test_mark_unanswered_queues_command():
    """POST /tickets/{id}/unanswered queues ticket.unanswered."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            TICKETS_URL + "/8/unanswered",
            headers=_auth(["ticket.markanswered"]),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "ticket.unanswered"


@pytest.mark.asyncio
async def test_add_collaborator_queues_command():
    """POST /tickets/{id}/collaborators queues ticket.collaborator.add."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            TICKETS_URL + "/6/collaborators",
            json={"user_id": 15},
            headers=_auth(["ticket.edit"]),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "ticket.collaborator.add"
    payload = json.loads(rows[0].payload)
    assert payload["user_id"] == 15


# ─────────────────────────────────────────────────────────────────────────────
# COMMAND RESPONSE SHAPE
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_command_response_shape():
    """All write endpoints return command_id (int) and status (str)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            TICKETS_URL + "/",
            json={"subject": "Shape test", "message": "Body."},
            headers={**_auth(["ticket.create"]), "Idempotency-Key": "shape-test-key"},
        )
    assert resp.status_code == 202
    data = resp.json()
    assert isinstance(data["command_id"], int)
    assert data["status"] == "pending"
    assert data["idempotency_key"] == "shape-test-key"


# ─────────────────────────────────────────────────────────────────────────────
# CROSS-CUTTING: wrong permission returns 403 but doesn't pollute inbox
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_wrong_permission_leaves_inbox_clean():
    """A 403 response must not create any inbox row."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post(
            TICKETS_URL + "/1/reply",
            json={"message": "Should not reach the inbox."},
            headers=_auth(["ticket.create"]),  # wrong perm — needs ticket.reply
        )
    rows = await _inbox_rows()
    assert len(rows) == 0


@pytest.mark.asyncio
async def test_all_write_endpoints_require_auth():
    """Every write endpoint returns 401 without a token."""
    endpoints = [
        ("POST",   "/"),
        ("POST",   "/1/reply"),
        ("POST",   "/1/note"),
        ("POST",   "/1/close"),
        ("POST",   "/1/reopen"),
        ("POST",   "/1/assign"),
        ("POST",   "/1/assign-team"),
        ("POST",   "/1/transfer"),
        ("POST",   "/1/change-owner"),
        ("PATCH",  "/1"),
        ("PATCH",  "/1/collaborators/7"),
        ("DELETE", "/1/collaborators/7"),
        ("PATCH",  "/1/thread/3"),
        ("POST",   "/1/thread/3/resend"),
        ("POST",   "/1/claim"),
        ("POST",   "/1/release"),
        ("POST",   "/1/refer"),
        ("DELETE", "/1"),
        ("POST",   "/1/merge"),
        ("POST",   "/1/answered"),
        ("POST",   "/1/unanswered"),
        ("POST",   "/1/collaborators"),
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        for method, path in endpoints:
            func = getattr(ac, method.lower())
            kwargs = {} if method == "DELETE" else {"json": {}}
            resp = await func(TICKETS_URL + path, **kwargs)
            assert resp.status_code == 401, (
                f"{method} {path} returned {resp.status_code}, expected 401"
            )


@pytest.mark.asyncio
async def test_claim_release_refer_queue_commands():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        claim = await ac.post(TICKETS_URL + "/5/claim", json={"note": "mine"}, headers=_auth(["ticket.assign"]))
        release = await ac.post(TICKETS_URL + "/5/release", json={"staff": True, "team": True},
                                headers=_auth(["ticket.release"]))
        refer = await ac.post(TICKETS_URL + "/5/refer", json={"team_id": 2}, headers=_auth(["ticket.assign"]))
    assert (claim.status_code, release.status_code, refer.status_code) == (202, 202, 202)
    rows = await _inbox_rows()
    assert [r.command_type for r in rows] == ["ticket.claim", "ticket.release", "ticket.refer"]
    assert json.loads(rows[1].payload) == {"ticket_id": 5, "staff": True, "team": True, "note": ""}
    assert json.loads(rows[2].payload)["team_id"] == 2


@pytest.mark.asyncio
async def test_release_and_refer_validate_and_check_permission():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        none = await ac.post(TICKETS_URL + "/5/release", json={}, headers=_auth(["ticket.release"]))
        two = await ac.post(TICKETS_URL + "/5/refer", json={"agent_id": 1, "team_id": 2}, headers=_auth(["ticket.assign"]))
        zero = await ac.post(TICKETS_URL + "/5/refer", json={}, headers=_auth(["ticket.assign"]))
        denied = await ac.post(TICKETS_URL + "/5/release", json={"staff": True}, headers=_auth(["ticket.assign"]))
    assert (none.status_code, two.status_code, zero.status_code, denied.status_code) == (422, 422, 422, 403)


@pytest.mark.asyncio
async def test_collaborator_list_is_scoped_and_404s_for_an_invisible_ticket():
    collab = AsyncMock(return_value=[{"id": 1, "user_id": 7, "name": "U", "email": "u@example.com",
                                      "is_active": 0, "is_cc": 1}])
    row = {"ticket_id": 9}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        with patch("app.modules.tickets.service.repo.get_ticket", new=AsyncMock(return_value=None)):
            missing = await ac.get(TICKETS_URL + "/9/collaborators", headers=_auth([]))
        with patch("app.modules.tickets.service.repo.get_ticket", new=AsyncMock(return_value=row)), \
             patch("app.modules.tickets.service.repo.get_ticket_collaborators", new=collab):
            ok = await ac.get(TICKETS_URL + "/9/collaborators", headers=_auth([]))
    assert missing.status_code == 404
    assert ok.json() == {"collaborators": [{"id": 1, "user_id": 7, "name": "U", "email": "u@example.com",
                                            "is_active": False, "is_cc": True}]}
    assert collab.await_args.kwargs["access"].staff_id == 5


@pytest.mark.asyncio
async def test_collaborator_update_and_remove_queue_commands():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        upd = await ac.patch(TICKETS_URL + "/5/collaborators/7", json={"is_active": False},
                             headers=_auth(["ticket.edit"]))
        rem = await ac.delete(TICKETS_URL + "/5/collaborators/7", headers=_auth(["ticket.edit"]))
        empty = await ac.patch(TICKETS_URL + "/5/collaborators/7", json={}, headers=_auth(["ticket.edit"]))
        denied = await ac.delete(TICKETS_URL + "/5/collaborators/7", headers=_auth(["ticket.reply"]))
    assert (upd.status_code, rem.status_code, empty.status_code, denied.status_code) == (202, 202, 422, 403)
    rows = await _inbox_rows()
    assert [r.command_type for r in rows] == ["ticket.collaborator.update", "ticket.collaborator.remove"]
    assert json.loads(rows[0].payload) == {"ticket_id": 5, "user_id": 7, "is_active": False}
    assert json.loads(rows[1].payload) == {"ticket_id": 5, "user_id": 7}


@pytest.mark.asyncio
async def test_collaborator_list_requires_auth():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        assert (await ac.get(TICKETS_URL + "/1/collaborators")).status_code == 401


@pytest.mark.asyncio
async def test_entry_edit_and_resend_queue_commands():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        edit = await ac.patch(TICKETS_URL + "/5/thread/8", json={"body": "fixed", "resend": True, "signature": "mine"},
                              headers=_auth(["ticket.reply"]))
        resend = await ac.post(TICKETS_URL + "/5/thread/8/resend", json={}, headers=_auth(["ticket.reply"]))
        empty = await ac.patch(TICKETS_URL + "/5/thread/8", json={"body": ""}, headers=_auth(["ticket.reply"]))
        badsig = await ac.post(TICKETS_URL + "/5/thread/8/resend", json={"signature": "x"}, headers=_auth(["ticket.reply"]))
        denied = await ac.patch(TICKETS_URL + "/5/thread/8", json={"body": "x"}, headers=_auth([]))
    assert (edit.status_code, resend.status_code, empty.status_code, badsig.status_code, denied.status_code) == (
        202, 202, 422, 422, 403)
    rows = await _inbox_rows()
    assert [r.command_type for r in rows] == ["ticket.entry.edit", "ticket.entry.resend"]
    assert json.loads(rows[0].payload) == {"ticket_id": 5, "entry_id": 8, "body": "fixed", "resend": True, "signature": "mine"}


@pytest.mark.asyncio
async def test_custom_fields_are_accepted_typed_and_queued():
    body = {"custom_fields": {"asset": "A-9", "urgent": True, "count": 3, "kinds": ["a", "b"], "note": ""}}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        upd = await ac.patch(TICKETS_URL + "/5", json=body, headers=_auth(["ticket.edit"]))
        new = await ac.post(TICKETS_URL + "/", json={"subject": "s", "message": "m", **body}, headers=_auth(["ticket.create"]))
        bad_name = await ac.patch(TICKETS_URL + "/5", json={"custom_fields": {"9bad": "x"}}, headers=_auth(["ticket.edit"]))
        bad_value = await ac.patch(TICKETS_URL + "/5", json={"custom_fields": {"a": {"nested": 1}}}, headers=_auth(["ticket.edit"]))
        too_many = await ac.patch(TICKETS_URL + "/5", json={"custom_fields": {f"f{i}": "x" for i in range(65)}},
                                  headers=_auth(["ticket.edit"]))
    assert (upd.status_code, new.status_code) == (202, 202)
    assert (bad_name.status_code, bad_value.status_code, too_many.status_code) == (422, 422, 422)
    rows = await _inbox_rows()
    assert json.loads(rows[0].payload)["custom_fields"] == body["custom_fields"]
    assert json.loads(rows[1].payload)["custom_fields"] == body["custom_fields"]
