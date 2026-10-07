"""The signed-in agent's own profile (osTicket's "My Profile", ``scp/profile.php``).

Permissions — any enabled agent (``require_staff()``), and only ever their *own* record: nothing here takes a
staff id, the bridge acts on the requesting staff member. This is the one place a write is not gated by a
permission key; ``tests/unit/test_bridge_registry.py`` lists the self-service commands explicitly.
Changing the password ends the agent's current API tokens (``/auth/login`` again).
"""

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_staff
from app.modules.profile import service
from app.modules.profile.models import CommandResponse, PasswordChange, Profile, ProfileUpdate

router = APIRouter(tags=["profile"])


@router.get("/profile", response_model=Profile, summary="My profile")
async def get_profile(
    current_user: dict = require_staff(),
    session: AsyncSession = Depends(get_session),
):
    profile = await service.get_profile(session, current_user["staff_id"])
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile


@router.patch("/profile", response_model=CommandResponse, status_code=202, summary="Update my profile")
async def update_profile(
    body: ProfileUpdate,
    current_user: dict = require_staff(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.update_profile(session, body, current_user["staff_id"], idempotency_key)


@router.post("/profile/password", response_model=CommandResponse, status_code=202, summary="Change my password")
async def change_password(
    body: PasswordChange,
    current_user: dict = require_staff(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.change_password(session, body, current_user["staff_id"], idempotency_key)
