"""Integration tests for KB and Canned Responses endpoints (Phase 7)."""

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

MOCK_CAT = {
    "id": 1,
    "name": "General Settings",
    "description": "Common questions",
    "is_public": True,
    "created": _NOW,
    "updated": _NOW,
    "notes": "Internal note",
}

MOCK_FAQ = {
    "id": 1,
    "category_id": 1,
    "question": "How to reset password?",
    "answer": "Click forgot password.",
    "is_published": True,
    "keywords": "password, reset",
    "notes": "",
    "created": _NOW,
    "updated": _NOW,
}

MOCK_CANNED = {
    "id": 1,
    "title": "Closing Ticket",
    "response": "Your ticket is now closed.",
    "is_enabled": True,
    "dept_id": 0,
    "notes": "Standard close",
    "created": _NOW,
    "updated": _NOW,
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


# ── KB Categories ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_categories_returns_200():
    with patch("app.modules.kb.service.repo.list_categories", new=AsyncMock(return_value=[MOCK_CAT])):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/kb/categories", headers=_auth(["kb.manage"]))
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["name"] == "General Settings"


@pytest.mark.asyncio
async def test_get_category_returns_detail():
    with patch("app.modules.kb.service.repo.get_category", new=AsyncMock(return_value=MOCK_CAT)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/kb/categories/1", headers=_auth(["kb.manage"]))
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "General Settings"
    assert data["notes"] == "Internal note"


@pytest.mark.asyncio
async def test_create_category_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/kb/categories",
            json={"name": "Billing"},
            headers=_auth(["kb.manage"]),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "kb.category.create"
    payload = json.loads(rows[0].payload)
    assert payload["name"] == "Billing"


@pytest.mark.asyncio
async def test_update_category_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.patch(
            "/api/v1/kb/categories/2",
            json={"description": "Updated"},
            headers=_auth(["kb.manage"]),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "kb.category.update"
    assert rows[0].aggregate_id == "2"


@pytest.mark.asyncio
async def test_delete_category_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.delete("/api/v1/kb/categories/3", headers=_auth(["kb.manage"]))
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "kb.category.delete"
    assert rows[0].aggregate_id == "3"


# ── FAQs ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_faqs_returns_200():
    with patch("app.modules.kb.service.repo.list_faqs", new=AsyncMock(return_value=[MOCK_FAQ])):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/kb/faqs", headers=_auth(["kb.manage"]))
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["question"] == "How to reset password?"


@pytest.mark.asyncio
async def test_get_faq_returns_detail():
    with patch("app.modules.kb.service.repo.get_faq", new=AsyncMock(return_value=MOCK_FAQ)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/kb/faqs/1", headers=_auth(["kb.manage"]))
    assert resp.status_code == 200
    data = resp.json()
    assert data["question"] == "How to reset password?"
    assert data["answer"] == "Click forgot password."


@pytest.mark.asyncio
async def test_create_faq_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/kb/faqs",
            json={"category_id": 1, "question": "Q?", "answer": "A!"},
            headers=_auth(["kb.manage"]),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "kb.faq.create"
    payload = json.loads(rows[0].payload)
    assert payload["question"] == "Q?"


@pytest.mark.asyncio
async def test_update_faq_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.patch(
            "/api/v1/kb/faqs/2",
            json={"answer": "New A!"},
            headers=_auth(["kb.manage"]),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "kb.faq.update"
    assert rows[0].aggregate_id == "2"


@pytest.mark.asyncio
async def test_delete_faq_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.delete("/api/v1/kb/faqs/3", headers=_auth(["kb.manage"]))
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "kb.faq.delete"
    assert rows[0].aggregate_id == "3"


# ── Canned Responses ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_canned_returns_200():
    with patch("app.modules.kb.service.repo.list_canned", new=AsyncMock(return_value=[MOCK_CANNED])):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/canned", headers=_auth(["canned.manage"]))
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["title"] == "Closing Ticket"


@pytest.mark.asyncio
async def test_get_canned_returns_detail():
    with patch("app.modules.kb.service.repo.get_canned", new=AsyncMock(return_value=MOCK_CANNED)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/canned/1", headers=_auth(["canned.manage"]))
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Closing Ticket"
    assert data["response"] == "Your ticket is now closed."


@pytest.mark.asyncio
async def test_create_canned_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/canned",
            json={"title": "Greeting", "response": "Hello!"},
            headers=_auth(["canned.manage"]),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "kb.canned.create"
    payload = json.loads(rows[0].payload)
    assert payload["title"] == "Greeting"
    assert payload["response"] == "Hello!"


@pytest.mark.asyncio
async def test_update_canned_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.patch(
            "/api/v1/canned/2",
            json={"title": "Updated Title"},
            headers=_auth(["canned.manage"]),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "kb.canned.update"
    assert rows[0].aggregate_id == "2"


@pytest.mark.asyncio
async def test_delete_canned_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.delete("/api/v1/canned/3", headers=_auth(["canned.manage"]))
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "kb.canned.delete"
    assert rows[0].aggregate_id == "3"


# ── Common checks ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_all_endpoints_require_auth():
    endpoints = [
        ("GET",    "/api/v1/kb/categories"),
        ("GET",    "/api/v1/kb/categories/1"),
        ("POST",   "/api/v1/kb/categories"),
        ("PATCH",  "/api/v1/kb/categories/1"),
        ("DELETE", "/api/v1/kb/categories/1"),
        ("GET",    "/api/v1/kb/faqs"),
        ("GET",    "/api/v1/kb/faqs/1"),
        ("POST",   "/api/v1/kb/faqs"),
        ("PATCH",  "/api/v1/kb/faqs/1"),
        ("DELETE", "/api/v1/kb/faqs/1"),
        ("GET",    "/api/v1/canned"),
        ("GET",    "/api/v1/canned/1"),
        ("POST",   "/api/v1/canned"),
        ("PATCH",  "/api/v1/canned/1"),
        ("DELETE", "/api/v1/canned/1"),
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        for method, path in endpoints:
            func = getattr(ac, method.lower())
            kwargs = {} if method in ("GET", "DELETE") else {"json": {}}
            resp = await func(path, **kwargs)
            assert resp.status_code == 401, f"{method} {path} → {resp.status_code}"
