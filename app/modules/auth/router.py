"""Auth router — login + /me.

Rate limits:
  POST /login — 10 requests/minute per IP (brute-force protection).
  GET  /me    — covered by the global 200/minute default.
"""

from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.security import OAuth2PasswordRequestForm

from app.core.auth import authenticate_with_osticket, create_access_token, get_current_user
from app.core.config import settings
from app.core.rate_limit import limiter

router = APIRouter(tags=["auth"])


@router.post("/login", summary="Authenticate with osTicket credentials")
@limiter.limit("10/minute")
async def login(
    request: Request,  # required by slowapi for rate-limit key extraction
    form_data: OAuth2PasswordRequestForm = Depends(),
):
    """Exchange staff username + password for a JWT Bearer token.

    - Uses ``application/x-www-form-urlencoded`` (OAuth2 password flow).
    - Credentials are forwarded to the PHP bridge for validation against
      osTicket's native ``Staff`` model.
    - Permissions are embedded in the JWT claims so no DB round-trip is
      needed on subsequent requests.
    - **Rate-limited to 10 requests/minute per IP** — returns ``429`` when exceeded.

    Returns:
        ``access_token``, ``token_type: "bearer"``, ``expires_in`` (seconds).
    """
    identity = await authenticate_with_osticket(form_data.username, form_data.password)
    token = create_access_token(identity, timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    }


@router.get("/me", summary="Return the current authenticated user's claims")
async def me(current_user: dict = Depends(get_current_user)):
    """Return JWT claims for the authenticated staff member.

    No DB round-trip — claims are read directly from the signed token.
    Returns the same shape as the ``/login`` response's decoded payload:
    ``staff_id``, ``username``, ``name``, ``email``, ``is_admin``, ``permissions``.
    """
    return current_user
