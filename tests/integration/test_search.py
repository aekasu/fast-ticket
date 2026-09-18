"""Integration tests for Search endpoints (Phase 9)."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


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
async def test_global_search_returns_200():
    mock_results = [
        {"id": 1, "type": "ticket", "title": "12345", "subtitle": "Help me"},
        {"id": 2, "type": "user", "title": "John Doe", "subtitle": "john@test.local"},
        {"id": 3, "type": "organization", "title": "Acme Corp", "subtitle": "Acme Corp"},
    ]
    with patch("app.modules.search.service.repo.search_all", new=AsyncMock(return_value=mock_results)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/search?q=test", headers=_auth())
    
    assert resp.status_code == 200
    data = resp.json()
    assert data["query"] == "test"
    assert data["total"] == 3
    assert len(data["results"]) == 3
    
    assert data["results"][0]["type"] == "ticket"
    assert data["results"][0]["url_path"] == "/tickets/1"
    
    assert data["results"][1]["type"] == "user"
    assert data["results"][1]["url_path"] == "/users/2"


@pytest.mark.asyncio
async def test_search_requires_auth():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/search?q=test")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_search_requires_minimum_length():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/search?q=a", headers=_auth())
    assert resp.status_code == 422  # validation error for min_length=2
