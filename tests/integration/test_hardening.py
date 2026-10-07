"""Token forgery, response headers, CORS and the docs switch."""

import os
import subprocess
import sys
import time

import jwt as pyjwt
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.core.security_headers import SecurityHeadersMiddleware
from app.main import app

ME = "/api/v1/auth/me"


def _token(claims=None, *, algorithm="HS256", key=None, drop=()):
    payload = {"staff_id": 5, "iss": "fast-ticket", "exp": int(time.time()) + 300, "jti": "x", **(claims or {})}
    for name in drop:
        payload.pop(name, None)
    return pyjwt.encode(payload, key if key is not None else settings.JWT_SECRET_KEY, algorithm=algorithm)


async def _get(path, token=None, **headers):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        auth = {"Authorization": f"Bearer {token}"} if token else {}
        return await client.get(path, headers={**auth, **headers})


# ── forged and malformed tokens ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_well_formed_token_is_the_control():
    assert (await _get(ME, _token())).status_code == 200


@pytest.mark.asyncio
async def test_an_unsigned_alg_none_token_is_refused():
    forged = pyjwt.encode({"staff_id": 1, "iss": "fast-ticket", "exp": int(time.time()) + 300}, key=None, algorithm="none")
    assert (await _get(ME, forged)).status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("algorithm", ["HS384", "HS512"])
async def test_a_token_signed_with_a_different_algorithm_is_refused_even_with_our_key(algorithm):
    """The algorithm is pinned; a token cannot pick its own."""
    assert (await _get(ME, _token(algorithm=algorithm, key=settings.JWT_SECRET_KEY * 2))).status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["exp", "iss", "staff_id"])
async def test_a_token_missing_a_required_claim_is_refused(missing):
    assert (await _get(ME, _token(drop=(missing,)))).status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("staff_id", ["5", True, False, 0, -3, 1.5, None, [5], {"id": 5}])
async def test_a_staff_id_that_is_not_a_positive_integer_is_refused_not_a_500(staff_id):
    assert (await _get(ME, _token({"staff_id": staff_id}))).status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["", "a", "a.b", "a.b.c", "Bearer x", "x" * 5000])
async def test_garbage_bearer_values_are_401_not_an_error(token):
    assert (await _get(ME, token or None)).status_code == 401


@pytest.mark.asyncio
async def test_non_ascii_bytes_in_the_authorization_header_are_401_not_an_error():
    """httpx will not send them as text, so send raw bytes: what an attacker's client can."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        for value in ("Bearer é" * 20, "Bearer \u00ff\u0001abc"):
            resp = await client.get(ME, headers=[(b"authorization", value.encode("latin-1", "replace"))])
            assert resp.status_code == 401


@pytest.mark.asyncio
async def test_the_authorization_header_must_be_a_bearer_token():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        for header in ("Basic dXNlcjpwdw==", "Token abc", "Bearer", ""):
            assert (await client.get(ME, headers={"Authorization": header})).status_code == 401


# ── response headers ──────────────────────────────────────────────────────────

EXPECTED = {
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
}


@pytest.mark.asyncio
async def test_a_normal_response_carries_the_headers():
    resp = await _get("/health")
    assert {k: resp.headers.get(k) for k in EXPECTED} == EXPECTED


@pytest.mark.asyncio
async def test_an_authentication_failure_carries_them_too():
    resp = await _get(ME)
    assert resp.status_code == 401
    assert {k: resp.headers.get(k) for k in EXPECTED} == EXPECTED


@pytest.mark.asyncio
async def test_a_413_from_an_inner_middleware_carries_them():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.post(
            "/api/v1/kb/categories", content=b"x" * (settings.MAX_REQUEST_BYTES + 1),
            headers={"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"},
        )
    assert resp.status_code == 413
    assert {k: resp.headers.get(k) for k in EXPECTED} == EXPECTED


@pytest.mark.asyncio
async def test_a_header_the_route_already_set_is_left_alone():
    inner = FastAPI()

    @inner.get("/x")
    async def x():
        from fastapi.responses import JSONResponse
        return JSONResponse({}, headers={"Cache-Control": "private, max-age=5"})

    inner.add_middleware(SecurityHeadersMiddleware)
    async with AsyncClient(transport=ASGITransport(app=inner), base_url="http://t") as client:
        resp = await client.get("/x")
    assert resp.headers["cache-control"] == "private, max-age=5"
    assert resp.headers["x-content-type-options"] == "nosniff"


# ── CORS ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_foreign_origin_is_not_granted_cross_origin_access():
    resp = await _get("/health", **{"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in resp.headers
    assert "access-control-allow-credentials" not in resp.headers


@pytest.mark.asyncio
async def test_cors_exposes_the_headers_a_browser_client_needs():
    # the test settings allow no origin, so look at how the middleware is configured instead of a response
    cors = next(m for m in app.user_middleware if m.cls.__name__ == "CORSMiddleware")
    exposed = {h.lower() for h in cors.kwargs["expose_headers"]}
    assert {"x-login-error", "retry-after", "content-disposition"} <= exposed
    assert cors.kwargs["allow_credentials"] is False


@pytest.mark.asyncio
async def test_a_preflight_from_a_foreign_origin_is_not_approved():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.options(
            "/api/v1/tickets/",
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST",
                     "Access-Control-Request-Headers": "authorization"},
        )
    assert "access-control-allow-origin" not in resp.headers


# ── the docs switch, and no debug mode ────────────────────────────────────────


def test_the_app_is_not_in_debug_mode():
    assert app.debug is False


def test_the_docs_can_be_switched_off():
    code = "from app.main import app; print(app.docs_url, app.redoc_url, app.openapi_url)"
    env = {**os.environ, "API_DOCS_ENABLED": "false"}
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
    assert out.stdout.strip().splitlines()[-1] == "None None None", out.stderr[-500:]


def test_the_docs_are_on_by_default():
    assert (app.docs_url, app.openapi_url) == ("/docs", "/openapi.json")
