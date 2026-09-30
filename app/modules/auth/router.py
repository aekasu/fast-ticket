"""Auth router — login + /me.

Rate limits:
  POST /login — 10 requests/minute per IP, plus a lockout after LOGIN_MAX_ATTEMPTS failed
                credentials for the same (username, address) — see app.core.login_throttle.
  GET  /me    — covered by the global 200/minute default.
"""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordRequestForm

from app.core.auth import (
    LoginRefused,
    authenticate_with_osticket,
    create_access_token,
    get_current_user,
)
from app.core.config import settings
from app.core.login_throttle import LoginThrottle
from app.core.rate_limit import client_ip, limiter

router = APIRouter(tags=["auth"])

login_throttle = LoginThrottle(settings.LOGIN_MAX_ATTEMPTS, settings.LOGIN_LOCKOUT_SECONDS)


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
    - **Rate-limited to 10 requests/minute per IP**, and an account/address pair that fails
      ``LOGIN_MAX_ATTEMPTS`` times is locked out for ``LOGIN_LOCKOUT_SECONDS`` — ``429`` with
      ``Retry-After``.
    - Refusals carry the reason in an ``X-Login-Error`` header: ``invalid_credentials`` (401),
      ``account_disabled`` (401), ``two_factor_required`` (403 — accounts with 2FA cannot use the
      API yet), ``password_change_required`` (403).

    Returns:
        ``access_token``, ``token_type: "bearer"``, ``expires_in`` (seconds).
    """
    address = client_ip(request)
    wait = login_throttle.retry_after(form_data.username, address)
    if wait:
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed logins for this account from your address. Try again in {wait} seconds.",
            headers={"Retry-After": str(wait)},
        )
    try:
        identity = await authenticate_with_osticket(form_data.username, form_data.password)
    except LoginRefused as refused:
        if refused.code == "invalid_credentials":   # only a wrong password is a strike
            login_throttle.record_failure(form_data.username, address)
        raise
    login_throttle.record_success(form_data.username, address)
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
