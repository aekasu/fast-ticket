"""Integration tests for the auth module (Phase 1).

Covers:
  - POST /api/v1/auth/login — happy path, bad credentials, bridge errors,
    response shape, permission/claims embedding, rate limit (429).
  - GET  /api/v1/auth/me   — token required, claims round-trip, token
    expiry and tampering (edge cases not duplicated from test_security.py).

All tests mock ``authenticate_with_osticket`` so no real osTicket or
PHP bridge is needed.
"""

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app.core.auth import create_access_token
from app.main import app

# ── Helpers ────────────────────────────────────────────────────────────────────

AUTH_URL = "/api/v1/auth/login"
ME_URL = "/api/v1/auth/me"

MOCK_IDENTITY = {
    "staff_id": 42,
    "username": "jdoe",
    "name": "Jane Doe",
    "email": "jdoe@example.local",
    "is_admin": False,
    "permissions": ["ticket.create", "ticket.reply", "task.create"],
}

ADMIN_IDENTITY = {**MOCK_IDENTITY, "is_admin": True, "permissions": []}


def _form(username: str = "jdoe", password: str = "secret") -> dict:
    """Build form-encoded login payload."""
    return {"username": username, "password": password}


def _valid_token(**overrides) -> str:
    identity = {**MOCK_IDENTITY, **overrides}
    return create_access_token(identity, expires_delta=timedelta(minutes=5))


# ── Rate-limiter reset fixture ─────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Reset in-memory rate limit counters before every test.

    Prevents one test's request count from bleeding into the next.
    """
    from app.core.rate_limit import limiter
    try:
        limiter._storage.reset()
    except Exception:
        pass  # storage may not support reset in all backends
    yield
    try:
        limiter._storage.reset()
    except Exception:
        pass


# ── Login — happy path ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_login_returns_200_and_token():
    """Successful login returns HTTP 200 with access_token."""
    with patch(
        "app.modules.auth.router.authenticate_with_osticket",
        new=AsyncMock(return_value=MOCK_IDENTITY),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(AUTH_URL, data=_form())
    assert resp.status_code == 200
    body = resp.json()
    assert "access_token" in body
    assert body["token_type"] == "bearer"
    assert isinstance(body["access_token"], str)
    assert len(body["access_token"]) > 20


@pytest.mark.asyncio
async def test_login_response_contains_expires_in():
    """Response includes expires_in field (in seconds)."""
    with patch(
        "app.modules.auth.router.authenticate_with_osticket",
        new=AsyncMock(return_value=MOCK_IDENTITY),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(AUTH_URL, data=_form())
    assert resp.status_code == 200
    body = resp.json()
    assert "expires_in" in body
    assert isinstance(body["expires_in"], int)
    assert body["expires_in"] > 0


@pytest.mark.asyncio
async def test_login_token_embeds_permissions():
    """The JWT returned by /login contains the staff's permission list."""
    import jwt as pyjwt
    from app.core.config import settings

    with patch(
        "app.modules.auth.router.authenticate_with_osticket",
        new=AsyncMock(return_value=MOCK_IDENTITY),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(AUTH_URL, data=_form())

    token = resp.json()["access_token"]
    claims = pyjwt.decode(
        token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM],
        options={"require": ["exp", "staff_id"]},
    )
    assert claims["staff_id"] == 42
    assert claims["username"] == "jdoe"
    assert "ticket.create" in claims["permissions"]
    assert "ticket.reply" in claims["permissions"]
    assert "task.create" in claims["permissions"]
    assert claims["is_admin"] is False


@pytest.mark.asyncio
async def test_login_admin_token_embeds_is_admin():
    """Admin identity results in is_admin=True in the token claims."""
    import jwt as pyjwt
    from app.core.config import settings

    with patch(
        "app.modules.auth.router.authenticate_with_osticket",
        new=AsyncMock(return_value=ADMIN_IDENTITY),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(AUTH_URL, data=_form())

    token = resp.json()["access_token"]
    claims = pyjwt.decode(
        token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM],
        options={"require": ["exp"]},
    )
    assert claims["is_admin"] is True


@pytest.mark.asyncio
async def test_login_empty_permissions_are_embedded():
    """Admins get an empty permissions list (they bypass all perm checks)."""
    import jwt as pyjwt
    from app.core.config import settings

    with patch(
        "app.modules.auth.router.authenticate_with_osticket",
        new=AsyncMock(return_value=ADMIN_IDENTITY),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(AUTH_URL, data=_form())

    claims = pyjwt.decode(
        resp.json()["access_token"], settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM], options={"require": ["exp"]},
    )
    assert claims["permissions"] == []


# ── Login — error cases ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_login_bad_credentials_returns_401():
    """Invalid username/password returns HTTP 401."""
    with patch(
        "app.modules.auth.router.authenticate_with_osticket",
        new=AsyncMock(side_effect=HTTPException(status_code=401, detail="Invalid username or password.")),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(AUTH_URL, data=_form(password="wrong"))
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_bridge_unreachable_returns_503():
    """If the PHP bridge is down, the endpoint returns HTTP 503."""
    with patch(
        "app.modules.auth.router.authenticate_with_osticket",
        new=AsyncMock(side_effect=HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(AUTH_URL, data=_form())
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_login_upstream_error_returns_502():
    """Unexpected bridge response (not 200/401) returns HTTP 502."""
    with patch(
        "app.modules.auth.router.authenticate_with_osticket",
        new=AsyncMock(side_effect=HTTPException(status_code=502, detail="Upstream authentication error.")),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(AUTH_URL, data=_form())
    assert resp.status_code == 502


@pytest.mark.asyncio
async def test_login_missing_username_returns_422():
    """Omitting username from the form returns 422 Unprocessable Entity."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(AUTH_URL, data={"password": "secret"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_login_missing_password_returns_422():
    """Omitting password from the form returns 422 Unprocessable Entity."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(AUTH_URL, data={"username": "jdoe"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_login_json_body_returns_422():
    """Sending JSON instead of form-encoded data returns 422 (wrong content-type)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(AUTH_URL, json={"username": "jdoe", "password": "secret"})
    assert resp.status_code == 422


# ── Login — rate limit ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_login_rate_limit_429_after_10_requests():
    """The 11th login request within one minute returns HTTP 429.

    The rate limit is 10/minute per IP.  slowapi uses '127.0.0.1' as the key
    when request.client is None (ASGITransport).
    """
    mock_auth = AsyncMock(return_value=MOCK_IDENTITY)
    with patch("app.modules.auth.router.authenticate_with_osticket", new=mock_auth):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            statuses = []
            for _ in range(11):
                r = await ac.post(AUTH_URL, data=_form())
                statuses.append(r.status_code)

    # First 10 must succeed (200); 11th must be rate-limited (429)
    assert all(s == 200 for s in statuses[:10]), f"Unexpected statuses: {statuses[:10]}"
    assert statuses[10] == 429, f"Expected 429, got {statuses[10]}"


@pytest.mark.asyncio
async def test_login_rate_limit_response_body():
    """Rate-limit response contains an error message."""
    mock_auth = AsyncMock(return_value=MOCK_IDENTITY)
    with patch("app.modules.auth.router.authenticate_with_osticket", new=mock_auth):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            for _ in range(10):
                await ac.post(AUTH_URL, data=_form())
            resp = await ac.post(AUTH_URL, data=_form())

    assert resp.status_code == 429
    # slowapi returns a JSON body with "error" key
    body = resp.json()
    assert "error" in body or "detail" in body


# ── /me — claims round-trip ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_me_returns_all_claim_fields():
    """/me returns the full set of JWT claims without a DB round-trip."""
    token = _valid_token()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(ME_URL, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["staff_id"] == MOCK_IDENTITY["staff_id"]
    assert data["username"] == MOCK_IDENTITY["username"]
    assert data["name"] == MOCK_IDENTITY["name"]
    assert data["email"] == MOCK_IDENTITY["email"]
    assert data["is_admin"] == MOCK_IDENTITY["is_admin"]
    assert set(data["permissions"]) == set(MOCK_IDENTITY["permissions"])


@pytest.mark.asyncio
async def test_me_admin_claims_are_accurate():
    """/me reflects is_admin=True for admin tokens."""
    token = _valid_token(**ADMIN_IDENTITY)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(ME_URL, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["is_admin"] is True


@pytest.mark.asyncio
async def test_me_without_token_returns_401():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(ME_URL)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_with_invalid_token_format_returns_401():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(ME_URL, headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_with_expired_token_returns_401():
    token = create_access_token(
        {**MOCK_IDENTITY},
        expires_delta=timedelta(seconds=-1),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(ME_URL, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert "expired" in resp.json().get("detail", "").lower()


@pytest.mark.asyncio
async def test_me_with_wrong_secret_token_returns_401():
    """Token signed with a different secret is rejected."""
    import warnings
    import jwt as pyjwt
    from datetime import UTC, datetime

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # suppress InsecureKeyLengthWarning — key is deliberately short
        forged = pyjwt.encode(
            {**MOCK_IDENTITY, "exp": datetime.now(UTC).timestamp() + 300},
            key="completely-wrong-secret",
            algorithm="HS256",
        )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(ME_URL, headers={"Authorization": f"Bearer {forged}"})
    assert resp.status_code == 401


# ── /me — missing required claims ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_me_token_missing_staff_id_returns_401():
    """JWT without staff_id claim is rejected (required: [exp, staff_id])."""
    import jwt as pyjwt
    from datetime import UTC, datetime
    from app.core.config import settings

    token = pyjwt.encode(
        {"username": "x", "exp": datetime.now(UTC).timestamp() + 300},
        key=settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(ME_URL, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
