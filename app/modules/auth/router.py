from datetime import timedelta

from fastapi import APIRouter, Depends
from fastapi.security import OAuth2PasswordRequestForm

from app.core.auth import authenticate_with_osticket, create_access_token, get_current_user
from app.core.config import settings

router = APIRouter(tags=["auth"])


@router.post("/login", summary="Authenticate with osTicket credentials")
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    """Exchange staff username + password for a JWT Bearer token.

    Uses HTTP form encoding (``application/x-www-form-urlencoded``).
    Credentials are validated against the osTicket PHP bridge.

    Rate-limited to 10 requests/minute per IP (enforced in middleware).
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

    No DB round-trip — claims are read directly from the token.
    """
    return current_user
