"""S0 — configuration and request-level protections."""

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.requests import Request as StarletteRequest

from app.core import config, rate_limit
from app.core.request_limits import BodySizeLimitMiddleware

STRONG = "s" * 48


# ── JWT secret ────────────────────────────────────────────────────────────────


def test_require_jwt_secret_aborts_when_the_secret_is_unset(monkeypatch):
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.setenv("REQUIRE_JWT_SECRET", "true")
    with pytest.raises(SystemExit):
        config._load_settings()


def test_require_jwt_secret_accepts_an_explicit_secret(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", STRONG)
    monkeypatch.setenv("REQUIRE_JWT_SECRET", "true")
    assert config._load_settings().JWT_SECRET_KEY == STRONG


def test_an_unset_secret_still_boots_in_dev_with_a_warning(monkeypatch, capsys):
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.setenv("REQUIRE_JWT_SECRET", "false")
    loaded = config._load_settings()
    assert len(loaded.JWT_SECRET_KEY) >= 32
    assert "JWT_SECRET_KEY is not set" in capsys.readouterr().err


@pytest.mark.parametrize("bad", ["short", "replace-with-48-byte-hex-secret" + "x" * 20])
def test_weak_or_placeholder_secrets_are_rejected(monkeypatch, bad):
    monkeypatch.setenv("JWT_SECRET_KEY", bad)
    with pytest.raises(ValidationError):
        config._load_settings()


def test_access_tokens_default_to_thirty_minutes(monkeypatch):
    monkeypatch.delenv("ACCESS_TOKEN_EXPIRE_MINUTES", raising=False)
    assert config.Settings().ACCESS_TOKEN_EXPIRE_MINUTES == 30


# ── client address ────────────────────────────────────────────────────────────


def _request(xff: str | None, peer: str = "10.0.0.9") -> StarletteRequest:
    headers = [(b"x-forwarded-for", xff.encode())] if xff else []
    return StarletteRequest({"type": "http", "headers": headers, "client": (peer, 1234)})


def test_forwarded_for_is_ignored_without_a_trusted_proxy(monkeypatch):
    monkeypatch.setattr(rate_limit.settings, "TRUSTED_PROXY_COUNT", 0)
    assert rate_limit.client_ip(_request("6.6.6.6")) == "10.0.0.9"


def test_the_client_is_the_nth_address_from_the_right(monkeypatch):
    # a spoofed leftmost entry must not become the client
    monkeypatch.setattr(rate_limit.settings, "TRUSTED_PROXY_COUNT", 1)
    assert rate_limit.client_ip(_request("6.6.6.6, 203.0.113.7")) == "203.0.113.7"
    monkeypatch.setattr(rate_limit.settings, "TRUSTED_PROXY_COUNT", 2)
    assert rate_limit.client_ip(_request("6.6.6.6, 203.0.113.7, 10.1.1.1")) == "203.0.113.7"


def test_fewer_hops_than_proxies_falls_back_to_the_socket_peer(monkeypatch):
    monkeypatch.setattr(rate_limit.settings, "TRUSTED_PROXY_COUNT", 2)
    assert rate_limit.client_ip(_request("203.0.113.7")) == "10.0.0.9"
    assert rate_limit.client_ip(_request(None)) == "10.0.0.9"


# ── the global limit is actually enforced ─────────────────────────────────────


@pytest.mark.asyncio
async def test_the_default_limit_returns_429_through_the_middleware():
    from slowapi import _rate_limit_exceeded_handler

    small = Limiter(key_func=rate_limit.client_ip, default_limits=["3/minute"])
    app = FastAPI()
    app.state.limiter = small
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)

    @app.get("/ping")
    async def ping(request: Request):
        return {"ok": True}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        codes = [(await client.get("/ping")).status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]


def test_the_real_app_installs_both_middlewares():
    from app.main import app

    installed = {m.cls for m in app.user_middleware}
    assert SlowAPIMiddleware in installed
    assert BodySizeLimitMiddleware in installed


# ── request size ──────────────────────────────────────────────────────────────


def _limited_app(max_bytes: int) -> FastAPI:
    app = FastAPI()
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=max_bytes)

    @app.post("/echo")
    async def echo(request: Request):
        return {"n": len(await request.body())}

    return app


@pytest.mark.asyncio
async def test_a_body_within_the_limit_passes():
    async with AsyncClient(transport=ASGITransport(app=_limited_app(100)), base_url="http://t") as c:
        resp = await c.post("/echo", content=b"x" * 50)
    assert (resp.status_code, resp.json()) == (200, {"n": 50})


@pytest.mark.asyncio
async def test_a_declared_oversize_body_is_refused_with_413():
    async with AsyncClient(transport=ASGITransport(app=_limited_app(100)), base_url="http://t") as c:
        resp = await c.post("/echo", content=b"x" * 500)
    assert resp.status_code == 413
    assert "exceeds 100 bytes" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_a_streamed_body_without_content_length_is_still_capped():
    async def chunks():
        for _ in range(10):
            yield b"x" * 50

    async with AsyncClient(transport=ASGITransport(app=_limited_app(100)), base_url="http://t") as c:
        resp = await c.post("/echo", content=chunks())
    assert resp.status_code == 413
