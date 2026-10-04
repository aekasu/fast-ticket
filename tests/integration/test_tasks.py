"""Integration tests for task endpoints (Phase 3).

Strategy mirrors Phase 2:
  - Reads  → mock repo functions (no real osTicket DB).
  - Writes → verify inbox row created with correct fields.
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

TASKS_URL = "/api/v1/tasks"

_NOW = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)

MOCK_TASK = {
    "task_id": 1,
    "number": "T-001",
    "title": "Investigate network latency",
    "status_id": 1,
    "status_name": "Open",
    "status_state": "open",
    "dept_id": 2,
    "dept_name": "IT",
    "assignee_staff_id": None,
    "assignee_name": None,
    "team_id": None,
    "team_name": None,
    "ticket_id": None,
    "created": _NOW,
    "updated": _NOW,
    "closed": None,
    "due_date": None,
}

MOCK_THREAD = {
    "id": 1,
    "type": "N",
    "title": "Progress update",
    "body": "Started investigating the switch logs.",
    "staff_id": 5,
    "user_id": None,
    "poster": "Test Agent",
    "ip_address": None,
    "created": _NOW,
}


# ── Token / auth helpers ───────────────────────────────────────────────────────

def _token(permissions: list[str], is_admin: bool = False) -> str:
    from datetime import timedelta
    from app.core.auth import create_access_token
    return create_access_token(
        {"staff_id": 5, "username": "agent", "name": "Test Agent",
         "email": "agent@test.local", "is_admin": is_admin, "permissions": permissions},
        expires_delta=timedelta(minutes=5),
    )

def _auth(perms: list[str] | None = None, is_admin: bool = False) -> dict:
    return {"Authorization": f"Bearer {_token(perms or [], is_admin)}"}

def _admin() -> dict:
    return _auth(is_admin=True)

async def _inbox_rows() -> list[IntegrationInbox]:
    async with TestingSessionLocal() as s:
        result = await s.execute(select(IntegrationInbox))
        return result.scalars().all()


# ─────────────────────────────────────────────────────────────────────────────
# READ TESTS
# ─────────────────────────────────────────────────────────────────────────────



@pytest.fixture(autouse=True)
def _total_of_the_list():
    """The list services also ask for the total; tests that care patch it themselves."""
    with patch("app.modules.tasks.service.repo.count_tasks", new=AsyncMock(return_value=0)) as count:
        yield count


@pytest.mark.asyncio
async def test_list_tasks_returns_200():
    with patch("app.modules.tasks.service.repo.list_tasks", new=AsyncMock(return_value=[MOCK_TASK])):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(TASKS_URL + "/", headers=_auth(["task.create"]))
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["task_id"] == 1
    assert data["items"][0]["title"] == "Investigate network latency"


@pytest.mark.asyncio
async def test_list_tasks_requires_auth():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(TASKS_URL + "/")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_listing_tasks_needs_no_permission_only_an_agent():
    """osTicket gates the task list on visibility, not on a permission: any enabled agent may ask."""
    with patch("app.modules.tasks.service.repo.list_tasks", new=AsyncMock(return_value=[])):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(TASKS_URL + "/", headers=_auth([]))
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_the_callers_access_is_what_scopes_the_task_reads():
    """The repository receives the caller's AccessContext — that is what narrows the query."""
    lst, one = AsyncMock(return_value=[]), AsyncMock(return_value=None)
    with patch("app.modules.tasks.service.repo.list_tasks", new=lst), \
         patch("app.modules.tasks.service.repo.get_task", new=one):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            await ac.get(TASKS_URL + "/", headers=_auth([]))
            resp = await ac.get(TASKS_URL + "/9", headers=_auth([]))
    assert lst.await_args.kwargs["access"].staff_id == 5
    assert one.await_args.kwargs["access"].staff_id == 5
    assert resp.status_code == 404          # a task the agent may not open looks like a task that is not there


@pytest.mark.asyncio
async def test_list_tasks_page_size_cap():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(TASKS_URL + "/", params={"page_size": 999}, headers=_auth(["task.create"]))
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_list_tasks_filter_by_ticket_id():
    """ticket_id query param is forwarded to the repo."""
    mock_list = AsyncMock(return_value=[])
    with patch("app.modules.tasks.service.repo.list_tasks", new=mock_list):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            await ac.get(TASKS_URL + "/", params={"ticket_id": 42}, headers=_auth(["task.create"]))
    mock_list.assert_called_once()
    _, kwargs = mock_list.call_args
    assert kwargs["ticket_id"] == 42


@pytest.mark.asyncio
async def test_get_task_returns_detail():
    with (
        patch("app.modules.tasks.service.repo.get_task", new=AsyncMock(return_value=MOCK_TASK)),
        patch("app.modules.tasks.service.repo.get_task_thread", new=AsyncMock(return_value=[MOCK_THREAD])),
        patch("app.modules.tasks.service.repo.get_task_attachments", new=AsyncMock(return_value=[])),
        patch("app.modules.tasks.service.repo.get_task_custom_fields", new=AsyncMock(return_value=[
            {"name": "site", "label": "Site", "type": "text", "value": "HQ", "value_id": None},
            {"name": "title", "label": "Title", "type": "text", "value": "dup", "value_id": None}])),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(TASKS_URL + "/1", headers=_auth(["task.create"]))
    assert resp.status_code == 200
    data = resp.json()
    assert data["task_id"] == 1
    assert len(data["thread"]) == 1
    assert data["thread"][0]["body"] == "Started investigating the switch logs."
    assert [c["name"] for c in data["custom_fields"]] == ["site"]


@pytest.mark.asyncio
async def test_get_task_not_found_returns_404():
    with patch("app.modules.tasks.service.repo.get_task", new=AsyncMock(return_value=None)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(TASKS_URL + "/9999", headers=_auth(["task.create"]))
    assert resp.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# WRITE TESTS
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_task_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            TASKS_URL + "/",
            json={"title": "Check server logs", "description": "Look for error patterns."},
            headers=_auth(["task.create"]),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "task.create"
    payload = json.loads(rows[0].payload)
    assert payload["title"] == "Check server logs"
    assert payload["description"] == "Look for error patterns."
    assert rows[0].requested_by_staff_id == 5


@pytest.mark.asyncio
async def test_create_task_linked_to_ticket():
    """ticket_id is forwarded into the payload when provided."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            TASKS_URL + "/",
            json={"title": "Follow-up", "description": "Chase the vendor.", "ticket_id": 77},
            headers=_auth(["task.create"]),
        )
    assert resp.status_code == 202
    payload = json.loads((await _inbox_rows())[0].payload)
    assert payload["ticket_id"] == 77


@pytest.mark.asyncio
async def test_create_task_validates_empty_title():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            TASKS_URL + "/",
            json={"title": ""},
            headers=_auth(["task.create"]),
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_task_requires_a_description():
    """osTicket's task form requires one; reject it at the API instead of failing in the bridge."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(TASKS_URL + "/", json={"title": "No body"}, headers=_auth(["task.create"]))
    assert resp.status_code == 422
    assert len(await _inbox_rows()) == 0


@pytest.mark.asyncio
async def test_create_task_requires_permission():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            TASKS_URL + "/",
            json={"title": "Test"},
            headers=_auth([]),
        )
    assert resp.status_code == 403
    assert len(await _inbox_rows()) == 0


@pytest.mark.asyncio
async def test_create_task_idempotency():
    headers = {**_auth(["task.create"]), "Idempotency-Key": "task-create-idem-1"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r1 = await ac.post(TASKS_URL + "/", json={"title": "Idempotent task", "description": "d"}, headers=headers)
        r2 = await ac.post(TASKS_URL + "/", json={"title": "Idempotent task", "description": "d"}, headers=headers)
    assert r1.json()["command_id"] == r2.json()["command_id"]
    assert len(await _inbox_rows()) == 1


@pytest.mark.asyncio
async def test_edit_task_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.patch(
            TASKS_URL + "/3",
            json={"title": "Updated title", "dept_id": 4},
            headers=_auth(["task.edit"]),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "task.edit"
    assert rows[0].aggregate_id == "3"
    payload = json.loads(rows[0].payload)
    assert payload["title"] == "Updated title"
    assert payload["dept_id"] == 4
    assert payload["task_id"] == 3


@pytest.mark.asyncio
async def test_assign_task_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            TASKS_URL + "/5/assign",
            json={"staff_id": 3, "note": "Best person for this."},
            headers=_auth(["task.assign"]),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "task.assign"
    payload = json.loads(rows[0].payload)
    assert payload["staff_id"] == 3
    assert payload["task_id"] == 5


@pytest.mark.asyncio
async def test_close_task_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            TASKS_URL + "/2/close",
            json={},
            headers=_auth(["task.close"]),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "task.close"
    assert rows[0].aggregate_id == "2"


@pytest.mark.asyncio
async def test_reopen_task_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            TASKS_URL + "/2/reopen",
            json={},
            headers=_auth(["task.close"]),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "task.reopen"


@pytest.mark.asyncio
async def test_add_note_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            TASKS_URL + "/4/note",
            json={"title": "Update", "note": "Checked logs, found nothing."},
            headers=_auth(["task.reply"]),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "task.note"
    payload = json.loads(rows[0].payload)
    assert payload["note"] == "Checked logs, found nothing."
    assert payload["task_id"] == 4


@pytest.mark.asyncio
async def test_add_note_validates_empty_body():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            TASKS_URL + "/4/note",
            json={"note": ""},
            headers=_auth(["task.reply"]),
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_delete_task_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.delete(TASKS_URL + "/9", headers=_auth(["task.delete"]))
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "task.delete"
    assert rows[0].aggregate_id == "9"


# ─────────────────────────────────────────────────────────────────────────────
# CROSS-CUTTING
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_command_response_shape():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            TASKS_URL + "/",
            json={"title": "Shape test", "description": "d"},
            headers={**_auth(["task.create"]), "Idempotency-Key": "task-shape-key"},
        )
    assert resp.status_code == 202
    data = resp.json()
    assert isinstance(data["command_id"], int)
    assert data["status"] == "pending"
    assert data["idempotency_key"] == "task-shape-key"


@pytest.mark.asyncio
async def test_wrong_permission_leaves_inbox_clean():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post(TASKS_URL + "/1/close", json={}, headers=_auth(["task.create"]))  # wrong perm
    assert len(await _inbox_rows()) == 0


@pytest.mark.asyncio
async def test_all_write_endpoints_require_auth():
    """Every write endpoint returns 401 without a token."""
    endpoints = [
        ("POST",   "/"),
        ("PATCH",  "/1"),
        ("POST",   "/1/assign"),
        ("POST",   "/1/claim"),
        ("POST",   "/1/transfer"),
        ("POST",   "/1/reply"),
        ("POST",   "/1/close"),
        ("POST",   "/1/reopen"),
        ("POST",   "/1/note"),
        ("DELETE", "/1"),
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        for method, path in endpoints:
            func = getattr(ac, method.lower())
            kwargs = {} if method == "DELETE" else {"json": {}}
            resp = await func(TASKS_URL + path, **kwargs)
            assert resp.status_code == 401, f"{method} {path} → {resp.status_code}"


@pytest.mark.asyncio
async def test_task_assign_takes_exactly_one_assignee_and_claim_transfer_reply_queue():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        staff = await ac.post(TASKS_URL + "/5/assign", json={"staff_id": 2}, headers=_auth(["task.assign"]))
        team = await ac.post(TASKS_URL + "/5/assign", json={"team_id": 3, "note": "n"}, headers=_auth(["task.assign"]))
        both = await ac.post(TASKS_URL + "/5/assign", json={"staff_id": 2, "team_id": 3}, headers=_auth(["task.assign"]))
        none = await ac.post(TASKS_URL + "/5/assign", json={}, headers=_auth(["task.assign"]))
        claim = await ac.post(TASKS_URL + "/5/claim", json={}, headers=_auth(["task.assign"]))
        move = await ac.post(TASKS_URL + "/5/transfer", json={"dept_id": 2, "note": "m"}, headers=_auth(["task.transfer"]))
        reply = await ac.post(TASKS_URL + "/5/reply", json={"message": "an update"}, headers=_auth(["task.reply"]))
        empty = await ac.post(TASKS_URL + "/5/reply", json={"message": ""}, headers=_auth(["task.reply"]))
        denied = await ac.post(TASKS_URL + "/5/transfer", json={"dept_id": 2}, headers=_auth(["task.assign"]))
    assert [r.status_code for r in (staff, team, claim, move, reply)] == [202] * 5
    assert (both.status_code, none.status_code, empty.status_code, denied.status_code) == (422, 422, 422, 403)
    rows = await _inbox_rows()
    assert [r.command_type for r in rows] == ["task.assign", "task.assign", "task.claim", "task.transfer", "task.reply"]
    assert json.loads(rows[1].payload) == {"task_id": 5, "team_id": 3, "note": "n"}
    assert json.loads(rows[3].payload) == {"task_id": 5, "dept_id": 2, "note": "m"}


@pytest.mark.asyncio
async def test_task_custom_fields_are_queued_on_create_and_edit():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        new = await ac.post(TASKS_URL + "/", json={"title": "t", "description": "d", "custom_fields": {"site": "HQ"}},
                            headers=_auth(["task.create"]))
        edit = await ac.patch(TASKS_URL + "/5", json={"custom_fields": {"site": "DC", "rack": 4}},
                              headers=_auth(["task.edit"]))
        bad = await ac.patch(TASKS_URL + "/5", json={"custom_fields": {"bad name": "x"}}, headers=_auth(["task.edit"]))
    assert (new.status_code, edit.status_code, bad.status_code) == (202, 202, 422)
    rows = await _inbox_rows()
    assert json.loads(rows[0].payload)["custom_fields"] == {"site": "HQ"}
    assert json.loads(rows[1].payload)["custom_fields"] == {"site": "DC", "rack": 4}
