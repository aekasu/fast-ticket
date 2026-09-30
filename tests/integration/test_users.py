"""Integration tests for users and organizations endpoints (Phase 4)."""

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

MOCK_ORG = {
    "id": 1,
    "name": "Acme Corp",
    "created": _NOW,
    "updated": _NOW,
    "address": "123 Coyote Way",
    "phone": "555-0100",
    "website": "www.acme.corp",
    "internal_notes": "Key account",
}

MOCK_USER = {
    "id": 1,
    "name": "Wile E. Coyote",
    "org_id": 1,
    "org_name": "Acme Corp",
    "status": 0,
    "created": _NOW,
    "updated": _NOW,
    "email": "wile@acme.corp",
    "phone": "555-0101",
    "mobile": "555-0102",
    "internal_notes": "VIP",
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


# ── Organizations ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_orgs_returns_200():
    with patch("app.modules.users.service.repo.list_organizations", new=AsyncMock(return_value=[MOCK_ORG])):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/orgs", headers=_auth(["user.dir"]))
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["name"] == "Acme Corp"


@pytest.mark.asyncio
async def test_get_org_returns_detail():
    with patch("app.modules.users.service.repo.get_organization", new=AsyncMock(return_value=MOCK_ORG)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/orgs/1", headers=_auth(["user.dir"]))
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Acme Corp"
    assert data["address"] == "123 Coyote Way"


@pytest.mark.asyncio
async def test_create_org_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/orgs",
            json={"name": "New Org", "website": "new.org"},
            headers=_auth(["org.create"]),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "org.create"
    payload = json.loads(rows[0].payload)
    assert payload["name"] == "New Org"


@pytest.mark.asyncio
async def test_update_org_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.patch(
            "/api/v1/orgs/2",
            json={"name": "Updated Org"},
            headers=_auth(["org.edit"]),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "org.update"
    assert rows[0].aggregate_id == "2"
    payload = json.loads(rows[0].payload)
    assert payload["name"] == "Updated Org"


@pytest.mark.asyncio
async def test_delete_org_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.delete("/api/v1/orgs/3", headers=_auth(["org.delete"]))
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "org.delete"
    assert rows[0].aggregate_id == "3"


# ── Users ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_users_returns_200():
    with patch("app.modules.users.service.repo.list_users", new=AsyncMock(return_value=[MOCK_USER])):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/users", headers=_auth(["user.dir"]))
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["name"] == "Wile E. Coyote"


@pytest.mark.asyncio
async def test_get_user_returns_detail():
    with patch("app.modules.users.service.repo.get_user", new=AsyncMock(return_value=MOCK_USER)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/users/1", headers=_auth(["user.dir"]))
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Wile E. Coyote"
    assert data["email"] == "wile@acme.corp"


@pytest.mark.asyncio
async def test_create_user_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/users",
            json={"name": "New User", "email": "new@user.com"},
            headers=_auth(["user.create"]),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "user.create"
    payload = json.loads(rows[0].payload)
    assert payload["name"] == "New User"


@pytest.mark.asyncio
async def test_update_user_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.patch(
            "/api/v1/users/4",
            json={"name": "Updated User"},
            headers=_auth(["user.edit"]),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "user.update"
    assert rows[0].aggregate_id == "4"


@pytest.mark.asyncio
async def test_delete_user_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.delete("/api/v1/users/5", headers=_auth(["user.delete"]))
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "user.delete"
    assert rows[0].aggregate_id == "5"


# ── Common checks ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_all_endpoints_require_auth():
    endpoints = [
        ("GET",    "/api/v1/orgs"),
        ("GET",    "/api/v1/orgs/1"),
        ("POST",   "/api/v1/orgs"),
        ("PATCH",  "/api/v1/orgs/1"),
        ("DELETE", "/api/v1/orgs/1"),
        ("GET",    "/api/v1/users"),
        ("GET",    "/api/v1/users/1"),
        ("POST",   "/api/v1/users"),
        ("PATCH",  "/api/v1/users/1"),
        ("DELETE", "/api/v1/users/1"),
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        for method, path in endpoints:
            func = getattr(ac, method.lower())
            kwargs = {} if method in ("GET", "DELETE") else {"json": {}}
            resp = await func(path, **kwargs)
            assert resp.status_code == 401, f"{method} {path} → {resp.status_code}"
