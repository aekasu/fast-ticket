"""Integration tests for Forms endpoints (Phase 10)."""

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

MOCK_FORM = {
    "id": 1,
    "title": "Contact Information",
    "type": "U",
    "is_deletable": False,
    "instructions": "Please provide details.",
    "notes": "User form",
    "created": _NOW,
    "updated": _NOW,
}

MOCK_FIELDS = [
    {
        "id": 1,
        "name": "phone",
        "label": "Phone Number",
        "type": "text",
        "is_required": False,
    },
]


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


# ── Forms ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_forms_returns_200():
    with patch("app.modules.forms.service.repo.list_forms", new=AsyncMock(return_value=[MOCK_FORM])):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/forms", headers=_auth(is_admin=True))
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["title"] == "Contact Information"


@pytest.mark.asyncio
async def test_get_form_returns_detail():
    with (
        patch("app.modules.forms.service.repo.get_form", new=AsyncMock(return_value=MOCK_FORM)),
        patch("app.modules.forms.service.repo.get_form_fields", new=AsyncMock(return_value=MOCK_FIELDS))
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/forms/1", headers=_auth(is_admin=True))
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Contact Information"
    assert len(data["fields"]) == 1
    assert data["fields"][0]["name"] == "phone"


@pytest.mark.asyncio
async def test_create_form_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/forms",
            json={"title": "Asset Detail", "type": "T"},
            headers=_auth(is_admin=True),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "form.create"
    payload = json.loads(rows[0].payload)
    assert payload["title"] == "Asset Detail"


@pytest.mark.asyncio
async def test_update_form_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.patch(
            "/api/v1/forms/2",
            json={"title": "Updated Title"},
            headers=_auth(is_admin=True),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "form.update"
    assert rows[0].aggregate_id == "2"


@pytest.mark.asyncio
async def test_delete_form_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.delete("/api/v1/forms/3", headers=_auth(is_admin=True))
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "form.delete"
    assert rows[0].aggregate_id == "3"


# ── Common checks ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_all_endpoints_require_auth():
    endpoints = [
        ("GET",    "/api/v1/forms"),
        ("GET",    "/api/v1/forms/1"),
        ("POST",   "/api/v1/forms"),
        ("PATCH",  "/api/v1/forms/1"),
        ("DELETE", "/api/v1/forms/1"),
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        for method, path in endpoints:
            func = getattr(ac, method.lower())
            kwargs = {} if method in ("GET", "DELETE") else {"json": {}}
            resp = await func(path, **kwargs)
            assert resp.status_code == 401, f"{method} {path} → {resp.status_code}"
