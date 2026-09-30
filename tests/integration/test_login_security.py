"""S1 — login refusals, the lockout, and token revocation."""

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import httpx
import jwt as pyjwt
import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app.core import auth
from app.core.auth import LoginRefused, create_access_token
from app.core.config import settings
from app.core.rate_limit import limiter
from app.main import app

LOGIN = "/api/v1/auth/login"
IDENTITY = {
    "staff_id": 7, "username": "jdoe", "name": "J Doe", "email": "j@x.test",
    "is_admin": False, "permissions": ["ticket.create"],
}


@pytest.fixture(autouse=True)
def _reset_limiter():
    limiter._storage.reset()
    yield
    limiter._storage.reset()


def _form(username="jdoe", password="pw"):
    return {"username": username, "password": password}


def _client(peer="203.0.113.5"):
    return AsyncClient(transport=ASGITransport(app=app, client=(peer, 4321)), base_url="http://test")


# ── the bridge's refusal codes ────────────────────────────────────────────────


class _FakeHTTP:
    def __init__(self, response: httpx.Response):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, *args, **kwargs):
        return self._response


def _bridge_says(status, body=None):
    response = httpx.Response(status, json=body) if body is not None else httpx.Response(status, text="boom")
    return patch("app.core.auth.httpx.AsyncClient", new=lambda **kw: _FakeHTTP(response))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status, code, http",
    [
        (401, "invalid_credentials", 401),
        (401, "account_disabled", 401),
        (403, "two_factor_required", 403),
        (403, "password_change_required", 403),
    ],
)
async def test_each_refusal_becomes_a_login_refused_with_its_code(status, code, http):
    with _bridge_says(status, {"error": "the bridge's own words", "code": code}):
        with pytest.raises(LoginRefused) as caught:
            await auth.authenticate_with_osticket("jdoe", "pw")
    assert caught.value.code == code
    assert caught.value.status_code == http
    assert caught.value.headers["X-Login-Error"] == code


@pytest.mark.asyncio
async def test_a_two_factor_refusal_says_why_in_the_bridges_words():
    message = "This account has two-factor authentication enabled, which the API does not support yet."
    with _bridge_says(403, {"error": message, "code": "two_factor_required"}):
        with pytest.raises(LoginRefused) as caught:
            await auth.authenticate_with_osticket("jdoe", "pw")
    assert "two-factor" in caught.value.detail


@pytest.mark.asyncio
async def test_a_bad_login_never_reveals_which_half_was_wrong():
    with _bridge_says(401, {"error": "anything the bridge said", "code": "invalid_credentials"}):
        with pytest.raises(LoginRefused) as caught:
            await auth.authenticate_with_osticket("jdoe", "pw")
    assert caught.value.detail == "Invalid username or password."


@pytest.mark.asyncio
async def test_a_bridge_without_codes_still_means_bad_credentials_on_401():
    with _bridge_says(401, {"error": "Invalid username or password."}):
        with pytest.raises(LoginRefused) as caught:
            await auth.authenticate_with_osticket("jdoe", "pw")
    assert caught.value.code == "invalid_credentials"


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{"error": "Invalid integration key.", "code": "invalid_integration_key"}, {"error": "x"}, None])
async def test_a_bridge_rejecting_our_key_is_our_problem_not_the_users(body):
    with _bridge_says(403, body):
        with pytest.raises(HTTPException) as caught:
            await auth.authenticate_with_osticket("jdoe", "pw")
    assert caught.value.status_code == 502
    assert not isinstance(caught.value, LoginRefused)


@pytest.mark.asyncio
async def test_the_login_route_surfaces_the_code_header():
    refused = LoginRefused(403, "two_factor_required", "2FA needed")
    with patch("app.modules.auth.router.authenticate_with_osticket", new=AsyncMock(side_effect=refused)):
        async with _client() as ac:
            resp = await ac.post(LOGIN, data=_form())
    assert resp.status_code == 403
    assert resp.headers["x-login-error"] == "two_factor_required"
    assert "2FA needed" in resp.json()["detail"]


# ── lockout through the real route ────────────────────────────────────────────


def _wrong():
    return AsyncMock(side_effect=LoginRefused(401, "invalid_credentials", "Invalid username or password."))


@pytest.mark.asyncio
async def test_repeated_wrong_passwords_lock_the_pair_out_with_retry_after():
    async with _client() as ac:
        with patch("app.modules.auth.router.authenticate_with_osticket", new=_wrong()):
            codes = [(await ac.post(LOGIN, data=_form())).status_code for _ in range(settings.LOGIN_MAX_ATTEMPTS)]
        assert codes == [401] * settings.LOGIN_MAX_ATTEMPTS

        # locked now: even the right password is not tried, and the bridge is not called
        good = AsyncMock(return_value=IDENTITY)
        with patch("app.modules.auth.router.authenticate_with_osticket", new=good):
            resp = await ac.post(LOGIN, data=_form())
    assert resp.status_code == 429
    assert int(resp.headers["retry-after"]) > 0
    assert "Try again in" in resp.json()["detail"]
    good.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_lockout_does_not_follow_the_user_to_another_address():
    with patch("app.modules.auth.router.authenticate_with_osticket", new=_wrong()):
        async with _client("6.6.6.6") as attacker:
            for _ in range(settings.LOGIN_MAX_ATTEMPTS):
                await attacker.post(LOGIN, data=_form())
    with patch("app.modules.auth.router.authenticate_with_osticket", new=AsyncMock(return_value=IDENTITY)):
        async with _client("203.0.113.5") as owner:
            resp = await owner.post(LOGIN, data=_form())
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_the_lockout_ignores_username_case():
    async with _client() as ac:
        with patch("app.modules.auth.router.authenticate_with_osticket", new=_wrong()):
            for name in ["JDOE", "jdoe", "Jdoe", "jDoE", "jdoe"][: settings.LOGIN_MAX_ATTEMPTS]:
                await ac.post(LOGIN, data=_form(username=name))
            resp = await ac.post(LOGIN, data=_form(username="JDoe"))
    assert resp.status_code == 429


@pytest.mark.asyncio
async def test_policy_refusals_are_not_strikes():
    """A 2FA account is refused every time, but that is not a guessing attack."""
    refused = AsyncMock(side_effect=LoginRefused(403, "two_factor_required", "2FA"))
    async with _client() as ac:
        with patch("app.modules.auth.router.authenticate_with_osticket", new=refused):
            for _ in range(settings.LOGIN_MAX_ATTEMPTS + 2):
                assert (await ac.post(LOGIN, data=_form())).status_code == 403
    # (10/minute per-IP limiter would trip at 11; 7 attempts stay under it)


@pytest.mark.asyncio
async def test_a_successful_login_clears_the_pairs_failures():
    async with _client() as ac:
        with patch("app.modules.auth.router.authenticate_with_osticket", new=_wrong()):
            for _ in range(settings.LOGIN_MAX_ATTEMPTS - 1):
                await ac.post(LOGIN, data=_form())
        with patch("app.modules.auth.router.authenticate_with_osticket", new=AsyncMock(return_value=IDENTITY)):
            assert (await ac.post(LOGIN, data=_form())).status_code == 200
        with patch("app.modules.auth.router.authenticate_with_osticket", new=_wrong()):
            assert (await ac.post(LOGIN, data=_form())).status_code == 401   # a fresh count of 1, not locked


# ── token claims and revocation ───────────────────────────────────────────────


def _claims(token):
    return pyjwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM], issuer=auth.TOKEN_ISSUER)


def test_tokens_carry_issuer_issued_at_and_a_unique_id():
    a = _claims(create_access_token(IDENTITY))
    b = _claims(create_access_token(IDENTITY))
    assert a["iss"] == "fast-ticket" and a["iat"] and a["exp"] > a["iat"]
    assert a["jti"] != b["jti"]


def _bearer(claims=None, secret=None):
    payload = {"staff_id": 7, "exp": 9999999999, **(claims or {})}
    return {"Authorization": "Bearer " + pyjwt.encode(payload, secret or settings.JWT_SECRET_KEY, algorithm="HS256")}


@pytest.mark.asyncio
async def test_a_token_from_another_issuer_is_refused():
    async with _client() as ac:
        assert (await ac.get("/api/v1/auth/me", headers=_bearer({"iss": "someone-else"}))).status_code == 401
        assert (await ac.get("/api/v1/auth/me", headers=_bearer())).status_code == 401   # no issuer at all


@pytest.mark.asyncio
async def test_a_token_signed_with_another_secret_is_refused():
    async with _client() as ac:
        resp = await ac.get("/api/v1/auth/me", headers=_bearer({"iss": "fast-ticket"}, secret="x" * 48))
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_a_valid_token_for_an_active_agent_works():
    token = create_access_token(IDENTITY, timedelta(minutes=5))
    async with _client() as ac:
        resp = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200 and resp.json()["staff_id"] == 7


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [False, None])   # disabled, or deleted
async def test_a_valid_token_stops_working_when_the_agent_is_disabled_or_deleted(state):
    token = create_access_token(IDENTITY, timedelta(minutes=5))
    with patch("app.core.auth.repo.get_staff_active", new=AsyncMock(return_value=state)):
        async with _client() as ac:
            resp = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert "no longer active" in resp.json()["detail"]
    assert resp.headers["www-authenticate"] == "Bearer"


@pytest.mark.asyncio
async def test_revocation_applies_to_permission_protected_routes_too():
    token = create_access_token({**IDENTITY, "permissions": ["commands.read"]}, timedelta(minutes=5))
    with patch("app.core.auth.repo.get_staff_active", new=AsyncMock(return_value=False)):
        async with _client() as ac:
            resp = await ac.get("/api/v1/commands", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
