"""Instance settings a client needs.

Permissions — any agent (``require_staff()``): these are limits the client must know to build a form, not
configuration.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_staff
from app.modules.settings import service
from app.modules.settings.models import AttachmentLimits

router = APIRouter(tags=["settings"])


@router.get("/settings/attachments", response_model=AttachmentLimits, summary="How big an attachment may be")
async def attachment_limits(
    current_user: dict = require_staff(),
    session: AsyncSession = Depends(get_session),
):
    return await service.attachment_limits(session)
