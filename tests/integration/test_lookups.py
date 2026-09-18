"""Integration tests for lookups (Topics, SLAs, Priorities) endpoints (Phase 6)."""

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

MOCK_TOPIC = {
    "id": 1,
    "topic": "General Inquiry",
    "is_active": True,
    "is_public": True,
    "created": _NOW,
    "updated": _NOW,
    "dept_id": 1,
    "priority_id": 1,
    "sla_id": 1,
    "page_id": 0,
    "flags": 0,
    "notes": "Default topic",
}

MOCK_SLA = {
    "id": 1,
    "name": "Standard SLA",
    "grace_period": 48,
    "is_active": True,
    "created": _NOW,
    "updated": _NOW,
    "flags": 0,
    "notes": "Default 48h SLA",
}

MOCK_PRIORITY = {
    "id": 1,
    "name": "Low",
    "desc": "Low Priority",
    "urgency": 1,
    "is_public": True,
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


# ── Help Topics ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_topics_returns_200():
    with patch("app.modules.lookups.service.repo.list_topics", new=AsyncMock(return_value=[MOCK_TOPIC])):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/topics", headers=_auth(["topic.manage"]))
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["topic"] == "General Inquiry"


@pytest.mark.asyncio
async def test_get_topic_returns_detail():
    with patch("app.modules.lookups.service.repo.get_topic", new=AsyncMock(return_value=MOCK_TOPIC)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/topics/1", headers=_auth(["topic.manage"]))
    assert resp.status_code == 200
    data = resp.json()
    assert data["topic"] == "General Inquiry"
    assert data["notes"] == "Default topic"


@pytest.mark.asyncio
async def test_create_topic_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/topics",
            json={"topic": "Billing"},
            headers=_auth(["topic.manage"]),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "topic.create"
    payload = json.loads(rows[0].payload)
    assert payload["topic"] == "Billing"


@pytest.mark.asyncio
async def test_update_topic_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.patch(
            "/api/v1/topics/2",
            json={"topic": "Support"},
            headers=_auth(["topic.manage"]),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "topic.update"
    assert rows[0].aggregate_id == "2"


@pytest.mark.asyncio
async def test_delete_topic_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.delete("/api/v1/topics/3", headers=_auth(["topic.manage"]))
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "topic.delete"
    assert rows[0].aggregate_id == "3"


# ── SLAs ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_slas_returns_200():
    with patch("app.modules.lookups.service.repo.list_slas", new=AsyncMock(return_value=[MOCK_SLA])):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/slas", headers=_auth(["sla.manage"]))
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["name"] == "Standard SLA"


@pytest.mark.asyncio
async def test_get_sla_returns_detail():
    with patch("app.modules.lookups.service.repo.get_sla", new=AsyncMock(return_value=MOCK_SLA)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/slas/1", headers=_auth(["sla.manage"]))
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Standard SLA"
    assert data["grace_period"] == 48


@pytest.mark.asyncio
async def test_create_sla_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/slas",
            json={"name": "Urgent", "grace_period": 2},
            headers=_auth(["sla.manage"]),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "sla.create"
    payload = json.loads(rows[0].payload)
    assert payload["name"] == "Urgent"
    assert payload["grace_period"] == 2


@pytest.mark.asyncio
async def test_update_sla_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.patch(
            "/api/v1/slas/2",
            json={"grace_period": 24},
            headers=_auth(["sla.manage"]),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "sla.update"
    assert rows[0].aggregate_id == "2"


@pytest.mark.asyncio
async def test_delete_sla_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.delete("/api/v1/slas/3", headers=_auth(["sla.manage"]))
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "sla.delete"
    assert rows[0].aggregate_id == "3"


# ── Priorities ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_priorities_returns_200():
    with patch("app.modules.lookups.service.repo.list_priorities", new=AsyncMock(return_value=[MOCK_PRIORITY])):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/priorities", headers=_auth(["priority.manage"]))
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["name"] == "Low"


@pytest.mark.asyncio
async def test_get_priority_returns_detail():
    with patch("app.modules.lookups.service.repo.get_priority", new=AsyncMock(return_value=MOCK_PRIORITY)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/priorities/1", headers=_auth(["priority.manage"]))
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Low"


@pytest.mark.asyncio
async def test_create_priority_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/priorities",
            json={"name": "Critical", "urgency": 4},
            headers=_auth(["priority.manage"]),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "priority.create"
    payload = json.loads(rows[0].payload)
    assert payload["name"] == "Critical"
    assert payload["urgency"] == 4


@pytest.mark.asyncio
async def test_update_priority_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.patch(
            "/api/v1/priorities/2",
            json={"name": "Medium"},
            headers=_auth(["priority.manage"]),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "priority.update"
    assert rows[0].aggregate_id == "2"


@pytest.mark.asyncio
async def test_delete_priority_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.delete("/api/v1/priorities/3", headers=_auth(["priority.manage"]))
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "priority.delete"
    assert rows[0].aggregate_id == "3"


# ── Common checks ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_all_endpoints_require_auth():
    endpoints = [
        ("GET",    "/api/v1/topics"),
        ("GET",    "/api/v1/topics/1"),
        ("POST",   "/api/v1/topics"),
        ("PATCH",  "/api/v1/topics/1"),
        ("DELETE", "/api/v1/topics/1"),
        ("GET",    "/api/v1/slas"),
        ("GET",    "/api/v1/slas/1"),
        ("POST",   "/api/v1/slas"),
        ("PATCH",  "/api/v1/slas/1"),
        ("DELETE", "/api/v1/slas/1"),
        ("GET",    "/api/v1/priorities"),
        ("GET",    "/api/v1/priorities/1"),
        ("POST",   "/api/v1/priorities"),
        ("PATCH",  "/api/v1/priorities/1"),
        ("DELETE", "/api/v1/priorities/1"),
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        for method, path in endpoints:
            func = getattr(ac, method.lower())
            kwargs = {} if method in ("GET", "DELETE") else {"json": {}}
            resp = await func(path, **kwargs)
            assert resp.status_code == 401, f"{method} {path} → {resp.status_code}"
