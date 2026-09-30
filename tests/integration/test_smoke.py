"""Smoke tests: app starts, health endpoint works, auth /me requires a token."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_health_endpoint_is_unauthenticated():
    """/health requires no token and returns {status: ok}."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_auth_me_requires_token():
    """/api/v1/auth/me returns 401 without a token."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/auth/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_auth_me_returns_claims_with_valid_token():
    """/api/v1/auth/me returns the caller's identity and current access for a valid token."""
    from datetime import timedelta
    from app.core.auth import create_access_token

    identity = {
        "staff_id": 7,
        "username": "smoketest",
        "name": "Smoke Tester",
        "email": "smoke@test.local",
        "is_admin": False,
        "permissions": ["ticket.create"],
    }
    token = create_access_token(identity, expires_delta=timedelta(minutes=5))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["staff_id"] == 7
    assert data["username"] == "smoketest"
    assert "ticket.create" in data["roles"]["1"]


@pytest.mark.asyncio
async def test_docs_endpoint_accessible():
    """OpenAPI docs are accessible without auth (dev convenience)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/docs")
    assert resp.status_code == 200
