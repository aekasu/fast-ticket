from datetime import timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timezone import database_timezone
from app.integration.osticket import read_repository as repo
from app.integration.osticket.bridge_read import bridge_json
from app.integration.osticket.commands_service import dispatch_command
from app.modules.system.models import CommandResponse, LogDelete, LogDetail, LogItem, LogList, SystemInfo


def _to_db_time(value):
    """A UTC instant as the naive datetime the database stores (in its own timezone)."""
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).astimezone(database_timezone()).replace(tzinfo=None)


def _item(row: dict) -> dict:
    return dict(id=row["id"], type=row["type"], title=row["title"], logger=row["logger"] or "",
                ip_address=row["ip_address"] or "", created=row["created"], updated=row["updated"])


async def list_logs(session: AsyncSession, *, log_type, since, until, sort, descending, page, page_size) -> LogList:
    page_size = min(page_size, 200)
    filters = dict(log_type=log_type, since=_to_db_time(since), until=_to_db_time(until))
    rows = await repo.list_logs(session, **filters, sort=sort, descending=descending, limit=page_size, offset=(page - 1) * page_size)
    return LogList(items=[LogItem(**_item(dict(r))) for r in rows], total=await repo.count_logs(session, **filters),
                   page=page, page_size=page_size)


async def get_log(session: AsyncSession, log_id: int) -> LogDetail | None:
    row = await repo.get_log(session, log_id)
    return LogDetail(**_item(row), log=row["log"] or "") if row else None


async def delete_logs(session: AsyncSession, body: LogDelete, staff_id: int, idempotency_key: str | None) -> CommandResponse:
    cmd = await dispatch_command(
        session=session, command_type="log.delete", aggregate_type="log", aggregate_id="",
        payload={"ids": sorted(set(body.ids))}, requested_by_staff_id=staff_id, idempotency_key=idempotency_key)
    return CommandResponse(command_id=cmd.id, status=cmd.status, idempotency_key=idempotency_key)


async def system_info(staff_id: int) -> SystemInfo:
    return SystemInfo(**await bridge_json("system.info", staff_id, {}))
