"""S4 — idempotency keys and the command inbox are per staff member."""

import json
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.auth import create_access_token
from app.core.scope import UNRESTRICTED
from app.integration.osticket.commands import IntegrationInbox
from app.integration.osticket.commands_service import (
    InvalidIdempotencyKey,
    client_idempotency_key,
    dispatch_command,
    scoped_idempotency_key,
)
from app.main import app
from tests.conftest import TestingSessionLocal, make_access


async def _dispatch(staff_id, key, **payload):
    async with TestingSessionLocal() as session:
        row = await dispatch_command(
            session=session, command_type="ticket.close", aggregate_type="ticket", aggregate_id="1",
            payload=payload or {"note": "x"}, requested_by_staff_id=staff_id, idempotency_key=key,
        )
        await session.commit()
        return row.id


async def _rows():
    async with TestingSessionLocal() as session:
        return (await session.execute(select(IntegrationInbox))).scalars().all()


# ── idempotency keys are per staff member ─────────────────────────────────────


@pytest.mark.asyncio
async def test_the_same_key_from_the_same_agent_returns_the_same_command():
    first, again = await _dispatch(5, "abc"), await _dispatch(5, "abc")
    assert first == again
    assert len(await _rows()) == 1


@pytest.mark.asyncio
async def test_the_same_key_from_two_agents_is_two_commands():
    """Otherwise agent B, reusing agent A's key, would be handed A's command (its id, status and result)."""
    a, b = await _dispatch(5, "shared"), await _dispatch(6, "shared")
    assert a != b
    rows = await _rows()
    assert sorted((r.requested_by_staff_id, r.idempotency_key) for r in rows) == [(5, "5:shared"), (6, "6:shared")]


@pytest.mark.asyncio
async def test_a_command_without_a_key_stores_none():
    await _dispatch(5, None)
    assert (await _rows())[0].idempotency_key is None


@pytest.mark.asyncio
async def test_an_oversize_key_is_refused_before_anything_is_queued():
    with pytest.raises(InvalidIdempotencyKey):
        await _dispatch(5, "k" * 201)
    assert await _rows() == []


@pytest.mark.asyncio
async def test_a_maximum_length_key_fits_the_column_once_prefixed():
    await _dispatch(123456, "k" * 200)
    assert len((await _rows())[0].idempotency_key) <= 255


def test_the_stored_key_can_be_turned_back_into_the_clients():
    assert client_idempotency_key(scoped_idempotency_key(5, "a:b")) == "a:b"
    assert client_idempotency_key(None) is None


@pytest.mark.asyncio
async def test_a_route_answers_422_for_an_oversize_key():
    token = create_access_token({"staff_id": 5, "username": "a", "name": "A", "email": "a@x.test", "permissions": ["canned.manage"]},
                                timedelta(minutes=5))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.post(
            "/api/v1/canned", json={"title": "t", "response": "r"},
            headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "k" * 300},
        )
    assert resp.status_code == 422 and "at most" in resp.json()["detail"]


# ── the command inbox: your own, or everyone's if you are an administrator ─────


def _headers():
    return {"Authorization": "Bearer " + create_access_token(
        {"staff_id": 5, "username": "a", "name": "A", "email": "a@x.test"}, timedelta(minutes=5))}


async def _as(access, path, **repo):
    with patch("app.core.auth.load_access_context", new=AsyncMock(return_value=access)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            return await client.get(path, headers=_headers())


ROW = {
    "id": 9, "command_type": "staff.create", "aggregate_type": "staff", "aggregate_id": "", "status": "done",
    "requested_by_staff_id": 5, "created_at": "2024-01-01T00:00:00", "processed_at": "2024-01-01T00:00:01",
    "payload": json.dumps({"username": "ann", "password": "hunter2"}), "idempotency_key": "5:my-key",
    "result_payload": json.dumps({"staff_id": 3, "token": "t0k"}), "error_message": None,
}


@pytest.mark.asyncio
async def test_a_plain_agent_lists_and_reads_only_their_own_commands():
    lst, one = AsyncMock(return_value=[]), AsyncMock(return_value=None)
    me = make_access(5, [])
    with patch("app.modules.commands.service.repo.list_commands", new=lst), \
         patch("app.modules.commands.service.repo.get_command", new=one):
        assert (await _as(me, "/api/v1/commands")).status_code == 200
        resp = await _as(me, "/api/v1/commands/9")
    assert lst.await_args.kwargs["access"] is me           # scoped to their own
    assert one.await_args.kwargs["access"] is me
    assert resp.status_code == 404                          # someone else's command looks like no command


@pytest.mark.asyncio
async def test_an_administrator_sees_every_command():
    lst = AsyncMock(return_value=[])
    with patch("app.modules.commands.service.repo.list_commands", new=lst):
        await _as(make_access(5, [], is_admin=True), "/api/v1/commands")
    assert lst.await_args.kwargs["access"] is UNRESTRICTED


@pytest.mark.asyncio
async def test_a_commands_secrets_are_masked_and_its_key_is_the_clients_own():
    with patch("app.modules.commands.service.repo.get_command", new=AsyncMock(return_value=dict(ROW))):
        resp = await _as(make_access(5, []), "/api/v1/commands/9")
    body = resp.json()
    assert json.loads(body["payload"]) == {"username": "ann", "password": "[redacted]"}
    assert json.loads(body["result_payload"]) == {"staff_id": 3, "token": "[redacted]"}
    assert body["idempotency_key"] == "my-key"              # not the internal "5:my-key"
    assert "hunter2" not in resp.text and "t0k" not in resp.text


@pytest.mark.asyncio
async def test_waiting_on_a_command_is_scoped_too():
    one = AsyncMock(return_value=None)
    with patch("app.modules.commands.service.repo.get_command", new=one):
        resp = await _as(make_access(5, []), "/api/v1/commands/9?wait=1")
    assert resp.status_code == 404 and one.await_args.kwargs["access"].staff_id == 5
