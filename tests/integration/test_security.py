"""Integration tests for the security dependency layer.

Covers require_perm() and require_admin() — the permission enforcement
dependencies used on every non-public route.

These tests use a protected dummy endpoint wired inside the test to avoid
coupling to any specific domain router.
"""

import pytest
from fastapi import APIRouter
from httpx import ASGITransport, AsyncClient

from app.core.security import require_admin, require_perm
from app.main import app

# ── Temporary test routes ──────────────────────────────────────────────────────
# We add a small router with sentinel endpoints so we can assert permission
# checks without depending on any real domain being implemented.

_security_test_router = APIRouter(prefix="/test-security")


@_security_test_router.get("/needs-ticket-create")
async def _needs_create(user=require_perm("ticket.create")):
    return {"ok": True}


@_security_test_router.get("/needs-admin")
async def _needs_admin(user=require_admin()):
    return {"ok": True}


app.include_router(_security_test_router, prefix="/api/v1")


# ── Fixtures ───────────────────────────────────────────────────────────────────


def _make_token(is_admin: bool = False, permissions: list[str] | None = None) -> str:
    from datetime import timedelta
    from app.core.auth import create_access_token
    return create_access_token(
        {
            "staff_id": 1,
            "username": "tester",
            "name": "T T",
            "email": "t@t.local",
            "is_admin": is_admin,
            "permissions": permissions or [],
        },
        expires_delta=timedelta(minutes=5),
    )


# ── require_perm tests ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_require_perm_passes_with_correct_permission():
    token = _make_token(permissions=["ticket.create"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/test-security/needs-ticket-create",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_require_perm_returns_403_when_permission_missing():
    token = _make_token(permissions=["task.create"])  # wrong perm
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/test-security/needs-ticket-create",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 403
    assert "ticket.create" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_require_perm_returns_403_with_empty_permissions():
    token = _make_token(permissions=[])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/test-security/needs-ticket-create",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_bypasses_require_perm():
    """is_admin=True bypasses any require_perm check."""
    token = _make_token(is_admin=True, permissions=[])  # no explicit perms
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/test-security/needs-ticket-create",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200


# ── require_admin tests ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_require_admin_passes_for_admin():
    token = _make_token(is_admin=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/test-security/needs-admin",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_require_admin_returns_403_for_non_admin():
    token = _make_token(is_admin=False, permissions=["ticket.create", "ticket.reply"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/test-security/needs-admin",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 403


# ── Token validation tests ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_token_returns_401():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/test-security/needs-ticket-create")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_malformed_token_returns_401():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/test-security/needs-ticket-create",
            headers={"Authorization": "Bearer not.a.jwt"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_tampered_token_returns_401():
    """Changing one character of the signature invalidates the token."""
    token = _make_token(permissions=["ticket.create"])
    header, payload, sig = token.rsplit(".", 2)
    tampered = f"{header}.{payload}.{'X' + sig[1:]}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/test-security/needs-ticket-create",
            headers={"Authorization": f"Bearer {tampered}"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_expired_token_returns_401():
    from datetime import timedelta
    from app.core.auth import create_access_token
    token = create_access_token(
        {"staff_id": 1, "username": "x", "name": "X", "email": "x@x.local",
         "is_admin": False, "permissions": ["ticket.create"]},
        expires_delta=timedelta(seconds=-1),  # already expired
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/test-security/needs-ticket-create",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 401
