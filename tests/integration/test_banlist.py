"""Integration tests for Banlist endpoints (Phase 8)."""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import select

from app.integration.osticket.commands import IntegrationInbox
from app.main import app
from tests.conftest import TestingSessionLocal

_NOW = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)

MOCK_BANLIST = {
    "id": 1,
    "val": "spammer@example.com",
    "is_active": True,
    "created": _NOW,
    "updated": _NOW,
    "notes": "Known spam domain",
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

async def _inbox_rows() -> list[IntegrationInbox]:
    async with TestingSessionLocal() as s:
        result = await s.execute(select(IntegrationInbox))
        return result.scalars().all()


# ── Banlist ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_banlist_returns_200():
    with patch("app.modules.banlist.service.repo.list_banlist", new=AsyncMock(return_value=[MOCK_BANLIST])):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/banlist", headers=_auth(is_admin=True))
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["val"] == "spammer@example.com"


@pytest.mark.asyncio
async def test_get_banlist_entry_returns_detail():
    with patch("app.modules.banlist.service.repo.get_banlist_entry", new=AsyncMock(return_value=MOCK_BANLIST)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/banlist/1", headers=_auth(is_admin=True))
    assert resp.status_code == 200
    data = resp.json()
    assert data["val"] == "spammer@example.com"
    assert data["notes"] == "Known spam domain"


@pytest.mark.asyncio
async def test_create_banlist_entry_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/banlist",
            json={"val": "new@spam.local"},
            headers=_auth(is_admin=True),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "banlist.create"
    payload = json.loads(rows[0].payload)
    assert payload["val"] == "new@spam.local"


@pytest.mark.asyncio
async def test_update_banlist_entry_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.patch(
            "/api/v1/banlist/2",
            json={"is_active": False},
            headers=_auth(is_admin=True),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "banlist.update"
    assert rows[0].aggregate_id == "2"


@pytest.mark.asyncio
async def test_delete_banlist_entry_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.delete("/api/v1/banlist/3", headers=_auth(is_admin=True))
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "banlist.delete"
    assert rows[0].aggregate_id == "3"


# ── Common checks ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_all_endpoints_require_auth():
    endpoints = [
        ("GET",    "/api/v1/banlist"),
        ("GET",    "/api/v1/banlist/1"),
        ("POST",   "/api/v1/banlist"),
        ("PATCH",  "/api/v1/banlist/1"),
        ("DELETE", "/api/v1/banlist/1"),
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        for method, path in endpoints:
            func = getattr(ac, method.lower())
            kwargs = {} if method in ("GET", "DELETE") else {"json": {}}
            resp = await func(path, **kwargs)
            assert resp.status_code == 401, f"{method} {path} → {resp.status_code}"
