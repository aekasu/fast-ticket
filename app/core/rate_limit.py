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

The limiter is also registered on ``app.state`` in ``app.main`` so that
slowapi's middleware can find it for the global default limit.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

# Global default: 200 requests/minute per IP.
# Individual endpoints can override with a tighter limit via @limiter.limit.
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["200/minute"],
)
