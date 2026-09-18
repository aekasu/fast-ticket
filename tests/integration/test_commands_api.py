"""Integration tests for Commands Inspection endpoints (Phase 11)."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


_NOW = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)

MOCK_COMMAND = {
    "id": 1,
    "command_type": "ticket.create",
    "aggregate_type": "ticket",
    "aggregate_id": "",
    "status": "pending",
    "requested_by_staff_id": 5,
    "created_at": _NOW,
    "processed_at": None,
    "payload": '{"subject": "Help me"}',
    "idempotency_key": "some-uuid",
    "result_payload": None,
    "error_message": None,
}


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


@pytest.mark.asyncio
async def test_list_commands_returns_200():
    with patch("app.modules.commands.service.repo.list_commands", new=AsyncMock(return_value=[MOCK_COMMAND])):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/commands", headers=_auth(["commands.read"]))
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["command_type"] == "ticket.create"


@pytest.mark.asyncio
async def test_get_command_returns_detail():
    with patch("app.modules.commands.service.repo.get_command", new=AsyncMock(return_value=MOCK_COMMAND)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/commands/1", headers=_auth(["commands.read"]))
    assert resp.status_code == 200
    data = resp.json()
    assert data["command_type"] == "ticket.create"
    assert data["payload"] == '{"subject": "Help me"}'


@pytest.mark.asyncio
async def test_commands_require_auth():
    endpoints = [
        ("GET", "/api/v1/commands"),
        ("GET", "/api/v1/commands/1"),
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        for method, path in endpoints:
            func = getattr(ac, method.lower())
            resp = await func(path)
            assert resp.status_code == 401, f"{method} {path} → {resp.status_code}"
