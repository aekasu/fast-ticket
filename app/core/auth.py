"""Authentication: osTicket bridge login + JWT mint/decode.

The permission set for each staff member is fetched from the osTicket DB at login
time and embedded in the JWT claims. This avoids a DB round-trip on every request;
the trade-off is that permission changes don't take effect until the next login.
Token expiry (ACCESS_TOKEN_EXPIRE_MINUTES) bounds the staleness window.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from app.core.config import settings

logger = logging.getLogger(__name__)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

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
    payload = {**identity, "exp": datetime.now(UTC) + expires_delta}
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and verify a JWT.  Raises HTTPException on failure."""
    try:
        return jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            options={"require": ["exp", "staff_id"]},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired.")
    except jwt.InvalidTokenError as exc:
        logger.warning("JWT validation failed: %s", exc)
        raise _CREDENTIALS_ERROR


# ── osTicket bridge auth ──────────────────────────────────────────────────────


async def authenticate_with_osticket(username: str, password: str) -> dict[str, Any]:
    """Validate staff credentials against the PHP bridge auth endpoint.

    Returns a dict with at minimum:
        staff_id, username, firstname, lastname, email, is_admin, permissions

    Raises HTTPException(401) on bad credentials or unreachable bridge.
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

    if response.status_code == 401:
        raise HTTPException(status_code=401, detail="Invalid username or password.")
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
        "permissions": list(data.get("permissions", [])),
    }


# ── FastAPI dependency ────────────────────────────────────────────────────────


async def get_current_user(token: str = Depends(oauth2_scheme)) -> dict[str, Any]:
    """Dependency: decode Bearer token, return claims dict.

    Raises 401 if token is missing, malformed, or expired.
    """
    return decode_access_token(token)


def authenticate_websocket(token: str | None) -> dict[str, Any]:
    """Authenticate a WebSocket connection synchronously.

    Used in the WebSocket endpoint where ``Depends`` is not available.
    Raises HTTPException(403) if token is invalid.
    """
    if not token:
        raise HTTPException(status_code=403, detail="WebSocket authentication required.")
    return decode_access_token(token)
