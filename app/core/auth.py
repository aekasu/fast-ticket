"""Authentication: osTicket bridge login + JWT mint/decode.

The login token proves who the caller is and carries no permissions. What they may do is
read from osTicket's tables on every request (``AccessContext``, app/core/access.py), so a
role change, a demotion or a disabled account takes effect on the very next request.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import load_access_context
from app.core.config import settings
from app.core.db import get_session

logger = logging.getLogger(__name__)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

TOKEN_ISSUER = "fast-ticket"

_CREDENTIALS_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid or expired credentials.",
    headers={"WWW-Authenticate": "Bearer"},
)


# ── JWT ───────────────────────────────────────────────────────────────────────


def create_access_token(identity: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    """Mint a signed JWT.

    Args:
        identity: Claims to embed.  Must include at least ``staff_id``.
        expires_delta: Override token lifetime.  Defaults to settings value.
    """
    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    now = datetime.now(UTC)
    payload = {
        **identity,
        "iss": TOKEN_ISSUER,
        "iat": now,
        "jti": uuid.uuid4().hex,
        "exp": now + expires_delta,
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and verify a JWT.  Raises HTTPException on failure."""
    try:
        return jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            issuer=TOKEN_ISSUER,
            options={"require": ["exp", "staff_id", "iss"]},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired.")
    except jwt.InvalidTokenError as exc:
        logger.warning("JWT validation failed: %s", exc)
        raise _CREDENTIALS_ERROR


# ── osTicket bridge auth ──────────────────────────────────────────────────────


class LoginRefused(HTTPException):
    """The bridge refused a login for a reason the caller can act on.

    ``code`` is the bridge's machine-readable reason (invalid_credentials, account_disabled,
    two_factor_required, password_change_required); it is also sent as an ``X-Login-Error``
    header so a client need not parse the message.
    """

    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(status_code=status_code, detail=message, headers={"X-Login-Error": code})
        self.code = code



async def authenticate_with_osticket(username: str, password: str) -> dict[str, Any]:
    """Validate staff credentials against the PHP bridge auth endpoint.

    Returns a dict with at minimum:
        staff_id, username, firstname, lastname, email, is_admin

    Raises ``LoginRefused`` when the bridge refuses the login (401/403 with a reason),
    HTTPException(503) if the bridge is unreachable and 502 if it misbehaves.
    """
    try:
        async with httpx.AsyncClient(timeout=settings.OSTICKET_AUTH_TIMEOUT) as client:
            response = await client.post(
                settings.OSTICKET_AUTH_URL,
                json={"username": username, "password": password},
                headers={"X-Integration-Key": settings.FASTAPI_INTEGRATION_KEY},
            )
    except httpx.TransportError as exc:
        logger.error("osTicket auth bridge unreachable: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Authentication service temporarily unavailable.",
        )

    if response.status_code in (401, 403):
        try:
            body = response.json()
        except ValueError:
            body = {}
        code = body.get("code")
        if code is None and response.status_code == 401:
            code = "invalid_credentials"   # a bridge older than the codes: 401 meant a bad login
        if code == "invalid_credentials":
            raise LoginRefused(401, code, "Invalid username or password.")
        if code in {"account_disabled", "two_factor_required", "password_change_required"}:
            raise LoginRefused(response.status_code, code, body.get("error") or "Login refused.")
        # A 403 with no known reason is the bridge rejecting *us* (wrong integration key): our
        # configuration, not the user's problem.
        logger.error("osTicket auth bridge rejected the request: %s %s", response.status_code, response.text[:200])
        raise HTTPException(status_code=502, detail="Upstream authentication error.")
    if response.status_code != 200:
        logger.error(
            "Unexpected response from osTicket auth bridge: %s %s",
            response.status_code,
            response.text[:200],
        )
        raise HTTPException(status_code=502, detail="Upstream authentication error.")

    data = response.json()
    return {
        "staff_id": data["staff_id"],
        "username": data["username"],
        "name": f"{data.get('firstname', '')} {data.get('lastname', '')}".strip(),
        "email": data.get("email", ""),
        "is_admin": bool(data.get("is_admin", False)),
    }


# ── FastAPI dependency ────────────────────────────────────────────────────────


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Dependency: verify the Bearer token, then load what the staff member may do *now*.

    The token proves who they are (and that we issued it); it carries no permissions. Access is read
    from osTicket's tables on every request (``AccessContext``), so disabling or deleting an agent, or
    changing their role, takes effect on their very next request. Raises 401 if the token is missing,
    malformed or expired, or the staff member is gone or disabled.

    Returns the identity plus ``access`` (the ``AccessContext``); ``require_perm`` and friends read
    that, and so should any service that needs to know what the caller may see.
    """
    claims = decode_access_token(token)
    access = await load_access_context(session, int(claims["staff_id"]))
    if access is None or not access.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This account is no longer active.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return {
        "staff_id": access.staff_id,
        "username": access.username,
        "name": access.name,
        "email": access.email,
        "is_admin": access.is_admin,
        "permissions": access.all_permissions(),
        "access": access,
    }


def authenticate_websocket(token: str | None) -> dict[str, Any]:
    """Authenticate a WebSocket connection synchronously.

    Used in the WebSocket endpoint where ``Depends`` is not available.
    Raises HTTPException(403) if token is invalid.
    """
    if not token:
        raise HTTPException(status_code=403, detail="WebSocket authentication required.")
    return decode_access_token(token)
