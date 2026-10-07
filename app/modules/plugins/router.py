"""Installed osTicket plugins, read-only — an Admin Panel page (``scp/plugins.php``, ``isAdmin()``): ``require_admin()``.

Installing, enabling, disabling or configuring a plugin runs the plugin's own code and is deliberately not exposed
(AGENTS.md: plugin registration)."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_admin
from app.modules.plugins import service
from app.modules.plugins.models import PluginList

router = APIRouter(tags=["plugins"])


@router.get("/plugins", response_model=PluginList, summary="Installed osTicket plugins (read-only)")
async def list_plugins(current_user: dict = require_admin(), session: AsyncSession = Depends(get_session)):
    return await service.list_plugins(session)
