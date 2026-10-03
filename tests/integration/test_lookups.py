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
    "flags": 0x2 | 0x1,         # active, custom numbers
    "notes": "Default topic",
    "parent_id": 0, "status_id": 0, "no_auto_response": 1, "sequence_id": 3, "number_format": "T-####",
    "assign_staff_id": 0, "assign_team_id": 4,
}

MOCK_SLA = {
    "id": 1,
    "name": "Standard SLA",
    "grace_period": 48,
    "is_active": True,
    "created": _NOW,
    "updated": _NOW,
    "schedule_id": 2, "transient": 1, "disable_overdue_alerts": 0,
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
            resp = await ac.get("/api/v1/topics", headers=_auth(is_admin=True))
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["topic"] == "General Inquiry"


@pytest.mark.asyncio
async def test_get_topic_returns_detail():
    with patch("app.modules.lookups.service.repo.get_topic", new=AsyncMock(return_value=dict(MOCK_TOPIC))), \
         patch("app.modules.lookups.service.repo.get_topic_form_ids", new=AsyncMock(return_value=[2, 5])):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/topics/1", headers=_auth(is_admin=True))
    assert resp.status_code == 200
    data = resp.json()
    assert data["topic"] == "General Inquiry"
    assert data["notes"] == "Default topic"
    assert (data["status"], data["custom_numbers"], data["no_auto_response"]) == ("active", True, True)
    assert (data["number_format"], data["assign_team_id"], data["form_ids"]) == ("T-####", 4, [2, 5])
    assert "flags" not in data


@pytest.mark.asyncio
async def test_topic_settings_are_queued_and_validated():
    body = {"status": "archived", "parent_id": 2, "status_id": 3, "no_auto_response": True, "custom_numbers": True,
            "number_format": "X-###", "sequence_id": 1, "assign_team_id": 4, "form_ids": [2, 9]}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        ok = await ac.patch("/api/v1/topics/2", json=body, headers=_auth(is_admin=True))
        both = await ac.patch("/api/v1/topics/2", json={"assign_staff_id": 1, "assign_team_id": 2}, headers=_auth(is_admin=True))
        status = await ac.patch("/api/v1/topics/2", json={"status": "gone"}, headers=_auth(is_admin=True))
        dup = await ac.patch("/api/v1/topics/2", json={"form_ids": [2, 2]}, headers=_auth(is_admin=True))
        zero = await ac.patch("/api/v1/topics/2", json={"form_ids": [0]}, headers=_auth(is_admin=True))
        agent = await ac.patch("/api/v1/topics/2", json={"status": "active"}, headers=_auth(["faq.manage"]))
    assert (ok.status_code, both.status_code, status.status_code, dup.status_code, zero.status_code,
            agent.status_code) == (202, 422, 422, 422, 422, 403)
    assert json.loads((await _inbox_rows())[0].payload) == {"topic_id": 2, **body}


@pytest.mark.asyncio
async def test_create_topic_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/topics",
            json={"topic": "Billing"},
            headers=_auth(is_admin=True),
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
            headers=_auth(is_admin=True),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "topic.update"
    assert rows[0].aggregate_id == "2"


@pytest.mark.asyncio
async def test_delete_topic_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.delete("/api/v1/topics/3", headers=_auth(is_admin=True))
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "topic.delete"
    assert rows[0].aggregate_id == "3"


# ── SLAs ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_slas_returns_200():
    with patch("app.modules.lookups.service.repo.list_slas", new=AsyncMock(return_value=[MOCK_SLA])):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/slas", headers=_auth(is_admin=True))
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["name"] == "Standard SLA"


@pytest.mark.asyncio
async def test_get_sla_returns_detail():
    with patch("app.modules.lookups.service.repo.get_sla", new=AsyncMock(return_value=MOCK_SLA)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/slas/1", headers=_auth(is_admin=True))
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Standard SLA"
    assert (data["schedule_id"], data["transient"], data["disable_overdue_alerts"]) == (2, True, False)
    assert "flags" not in data
    assert data["grace_period"] == 48


@pytest.mark.asyncio
async def test_create_sla_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/slas",
            json={"name": "Urgent", "grace_period": 2},
            headers=_auth(is_admin=True),
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
            headers=_auth(is_admin=True),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "sla.update"
    assert rows[0].aggregate_id == "2"


@pytest.mark.asyncio
async def test_delete_sla_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.delete("/api/v1/slas/3", headers=_auth(is_admin=True))
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "sla.delete"
    assert rows[0].aggregate_id == "3"


# ── Priorities ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_priorities_returns_200():
    with patch("app.modules.lookups.service.repo.list_priorities", new=AsyncMock(return_value=[MOCK_PRIORITY])):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/priorities", headers=_auth(is_admin=True))
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["name"] == "Low"


@pytest.mark.asyncio
async def test_get_priority_returns_detail():
    with patch("app.modules.lookups.service.repo.get_priority", new=AsyncMock(return_value=MOCK_PRIORITY)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/priorities/1", headers=_auth(is_admin=True))
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Low"


@pytest.mark.asyncio
async def test_create_priority_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/priorities",
            json={"name": "Critical", "urgency": 4},
            headers=_auth(is_admin=True),
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
            headers=_auth(is_admin=True),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "priority.update"
    assert rows[0].aggregate_id == "2"


@pytest.mark.asyncio
async def test_delete_priority_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.delete("/api/v1/priorities/3", headers=_auth(is_admin=True))
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


@pytest.mark.asyncio
async def test_sla_settings_are_queued_and_validated():
    body = {"grace_period": 24, "schedule_id": 2, "transient": True, "disable_overdue_alerts": False, "is_active": False}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        ok = await ac.patch("/api/v1/slas/3", json=body, headers=_auth(is_admin=True))
        huge = await ac.patch("/api/v1/slas/3", json={"grace_period": 9000}, headers=_auth(is_admin=True))
        neg = await ac.patch("/api/v1/slas/3", json={"schedule_id": -1}, headers=_auth(is_admin=True))
        agent = await ac.patch("/api/v1/slas/3", json={"transient": True}, headers=_auth(["faq.manage"]))
    assert (ok.status_code, huge.status_code, neg.status_code, agent.status_code) == (202, 422, 422, 403)
    assert json.loads((await _inbox_rows())[0].payload) == {"sla_id": 3, **body}
