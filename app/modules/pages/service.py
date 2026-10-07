from sqlalchemy.ext.asyncio import AsyncSession

from app.integration.osticket import read_repository as repo
from app.integration.osticket.commands_service import dispatch_command
from app.modules.pages.models import CommandResponse, PageCreate, PageDetail, PageItem, PageList, PageUpdate


def _item(row: dict) -> dict:
    return dict(id=row["id"], name=row["name"], type=row["type"], is_active=bool(row["is_active"]),
                in_use=bool(row["in_use"]), created=row["created"], updated=row["updated"])


async def list_pages(session: AsyncSession, *, page: int, page_size: int) -> PageList:
    rows = await repo.list_pages(session, limit=page_size, offset=(page - 1) * page_size)
    return PageList(items=[PageItem(**_item(dict(r))) for r in rows], total=await repo.count_pages(session), page=page, page_size=page_size)


async def get_page(session: AsyncSession, page_id: int) -> PageDetail | None:
    row = await repo.get_page(session, page_id)
    return PageDetail(**_item(row), body=row["body"] or "", notes=row["notes"] or "") if row else None


def _response(cmd, idempotency_key) -> CommandResponse:
    return CommandResponse(command_id=cmd.id, status=cmd.status, idempotency_key=idempotency_key)


async def create_page(session: AsyncSession, body: PageCreate, staff_id: int, idempotency_key: str | None) -> CommandResponse:
    cmd = await dispatch_command(
        session=session, command_type="page.create", aggregate_type="page", aggregate_id="",
        payload=body.model_dump(), requested_by_staff_id=staff_id, idempotency_key=idempotency_key)
    return _response(cmd, idempotency_key)


async def update_page(session: AsyncSession, page_id: int, body: PageUpdate, staff_id: int, idempotency_key: str | None) -> CommandResponse:
    cmd = await dispatch_command(
        session=session, command_type="page.update", aggregate_type="page", aggregate_id=str(page_id),
        payload={"page_id": page_id, **body.model_dump(exclude_none=True)}, requested_by_staff_id=staff_id, idempotency_key=idempotency_key)
    return _response(cmd, idempotency_key)


async def delete_page(session: AsyncSession, page_id: int, staff_id: int, idempotency_key: str | None) -> CommandResponse:
    cmd = await dispatch_command(
        session=session, command_type="page.delete", aggregate_type="page", aggregate_id=str(page_id),
        payload={"page_id": page_id}, requested_by_staff_id=staff_id, idempotency_key=idempotency_key)
    return _response(cmd, idempotency_key)
