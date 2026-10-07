"""System logs and system information — Admin Panel pages (``scp/logs.php``, ``scp/system.php``,
``isAdmin()``), so every route is ``require_admin()``."""

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_admin
from app.core.timezone import UtcInput
from app.modules.system import service
from app.modules.system.models import CommandResponse, LogDelete, LogDetail, LogList, LogSort, LogType, SystemInfo

router = APIRouter(tags=["system"])


@router.get("/logs", response_model=LogList, summary="System logs")
async def list_logs(
    type: LogType | None = Query(default=None, description="Debug, Warning or Error"),
    since: UtcInput | None = Query(default=None, description="Entries created at or after this instant"),
    until: UtcInput | None = Query(default=None, description="Entries created at or before this instant"),
    sort: LogSort = Query(default="id"),
    descending: bool = Query(default=True),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
):
    if since and until and since > until:
        raise HTTPException(status_code=422, detail="since is after until")
    return await service.list_logs(session, log_type=type, since=since, until=until, sort=sort,
                                   descending=descending, page=page, page_size=page_size)


@router.get("/logs/{log_id}", response_model=LogDetail, summary="One system log entry")
async def get_log(
    log_id: int,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
):
    log = await service.get_log(session, log_id)
    if log is None:
        raise HTTPException(status_code=404, detail="Log entry not found")
    return log


@router.post("/logs/delete", response_model=CommandResponse, status_code=202, summary="Delete log entries")
async def delete_logs(
    body: LogDelete,
    current_user: dict = require_admin(),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.delete_logs(session, body, current_user["staff_id"], idempotency_key)


@router.get("/system/info", response_model=SystemInfo, summary="About this osTicket installation")
async def system_info(current_user: dict = require_admin()):
    return await service.system_info(current_user["staff_id"])
