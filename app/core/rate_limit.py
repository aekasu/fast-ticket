"""Shared rate limiter singleton.

Defined here (not in main.py) so that routers can import and apply
per-endpoint limits via the ``@limiter.limit(...)`` decorator without
creating a circular import with ``app.main``.

Usage in a router:
    from fastapi import Request
    from app.core.rate_limit import limiter

    @router.post("/login")
    @limiter.limit("10/minute")
    async def login(request: Request, ...):
        ...

The default limit (``RATE_LIMIT_DEFAULT``) applies to every HTTP route through
``SlowAPIMiddleware`` (added in ``app.main``); slowapi only enforces default limits when that
middleware is installed.
"""

import math
import time

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.config import settings


def client_ip(request: Request) -> str:
    """The address rate limits and login lockouts are keyed on.

    Behind ``TRUSTED_PROXY_COUNT`` proxies the client is the Nth entry from the right of
    X-Forwarded-For (each proxy appends the address it saw; anything to the left is client-supplied
    and spoofable). With no trusted proxy the header is ignored entirely.
    """
    peer = request.client.host if request.client else "unknown"
    trusted = settings.TRUSTED_PROXY_COUNT
    if trusted <= 0:
        return peer
    hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
    if len(hops) < trusted:
        return peer   # fewer hops than proxies configured: the header can't be trusted
    return hops[-trusted]


limiter = Limiter(
    key_func=client_ip,
    default_limits=[settings.RATE_LIMIT_DEFAULT],
    storage_uri=settings.RATE_LIMIT_STORAGE_URI,
)


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """429 in the API's own error shape (``detail``) with ``Retry-After``: the seconds until the window frees a slot.

    slowapi's stock handler answers ``{"error": ...}`` and sends ``Retry-After`` only when its header
    injection is enabled (which also adds ``X-RateLimit-*`` to every response); this one sets just what a
    client needs.
    """
    retry_after = 60
    try:
        item, args = request.state.view_rate_limit
        retry_after = item.get_expiry()   # the whole window, if the exact reset cannot be read
        reset_at = request.app.state.limiter.limiter.get_window_stats(item, *args)[0]
        retry_after = max(1, math.ceil(reset_at - time.time()))
    except Exception:   # never let the 429 itself fail
        pass
    return JSONResponse(
        {"detail": f"Rate limit exceeded: {exc.detail}"},
        status_code=429,
        headers={"Retry-After": str(retry_after)},
    )
