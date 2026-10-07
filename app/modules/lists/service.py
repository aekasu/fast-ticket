from sqlalchemy.ext.asyncio import AsyncSession

from app.integration.osticket import read_repository as repo
from app.integration.osticket.commands_service import dispatch_command
from app.modules.lists.models import (
    CommandResponse, CustomListItem, CustomListResponse, EntryCreate, EntryUpdate, ListCreate, ListEntry, ListEntryResponse, ListUpdate)

_MASK_EDIT, _MASK_ADD, _MASK_DELETE = 0x1, 0x2, 0x4   # DynamicList::MASK_*: a set bit takes the ability away


def _list(row: dict) -> CustomListItem:
    masks = int(row["masks"] or 0)
    return CustomListItem(
        id=row["id"], name=row["name"], name_plural=row["name_plural"] or "", sort_mode=row["sort_mode"], notes=row["notes"] or "",
        item_count=int(row["item_count"] or 0), handled_by=row["type"] or None,
        is_editable=not masks & _MASK_EDIT and not row["type"], allow_add=not masks & _MASK_ADD and not row["type"],
        is_deletable=not masks & _MASK_DELETE and not row["type"], created=row["created"], updated=row["updated"])


async def list_lists(session: AsyncSession, *, page: int, page_size: int) -> CustomListResponse:
    rows = await repo.list_custom_lists(session, limit=page_size, offset=(page - 1) * page_size)
    return CustomListResponse(items=[_list(dict(r)) for r in rows], total=await repo.count_custom_lists(session),
                              page=page, page_size=page_size)


async def get_list(session: AsyncSession, list_id: int) -> CustomListItem | None:
    row = await repo.get_custom_list(session, list_id)
    return _list(row) if row else None


async def list_entries(session: AsyncSession, list_id: int, *, page: int, page_size: int) -> ListEntryResponse | None:
    if await repo.get_custom_list(session, list_id) is None:
        return None
    rows = await repo.list_custom_list_items(session, list_id, limit=page_size, offset=(page - 1) * page_size)
    return ListEntryResponse(
        items=[ListEntry(id=r["id"], value=r["value"], abbrev=r["abbrev"] or "", sort=r["sort"], is_enabled=bool(r["status"] & 1),
                         is_internal=bool(r["status"] & 2)) for r in map(dict, rows)],
        total=await repo.count_custom_list_items(session, list_id), page=page, page_size=page_size)


def _response(cmd, idempotency_key) -> CommandResponse:
    return CommandResponse(command_id=cmd.id, status=cmd.status, idempotency_key=idempotency_key)


async def create_list(session: AsyncSession, body: ListCreate, staff_id: int, key: str | None) -> CommandResponse:
    cmd = await dispatch_command(
        session=session, command_type="list.create", aggregate_type="list", aggregate_id="",
        payload=body.model_dump(), requested_by_staff_id=staff_id, idempotency_key=key)
    return _response(cmd, key)


async def update_list(session: AsyncSession, list_id: int, body: ListUpdate, staff_id: int, key: str | None) -> CommandResponse:
    cmd = await dispatch_command(
        session=session, command_type="list.update", aggregate_type="list", aggregate_id=str(list_id),
        payload={"list_id": list_id, **body.model_dump(exclude_none=True)}, requested_by_staff_id=staff_id, idempotency_key=key)
    return _response(cmd, key)


async def delete_list(session: AsyncSession, list_id: int, staff_id: int, key: str | None) -> CommandResponse:
    cmd = await dispatch_command(
        session=session, command_type="list.delete", aggregate_type="list", aggregate_id=str(list_id),
        payload={"list_id": list_id}, requested_by_staff_id=staff_id, idempotency_key=key)
    return _response(cmd, key)


async def create_entry(session: AsyncSession, list_id: int, body: EntryCreate, staff_id: int, key: str | None) -> CommandResponse:
    cmd = await dispatch_command(
        session=session, command_type="list.item.create", aggregate_type="list", aggregate_id=str(list_id),
        payload={"list_id": list_id, **body.model_dump()}, requested_by_staff_id=staff_id, idempotency_key=key)
    return _response(cmd, key)


async def update_entry(session: AsyncSession, list_id: int, item_id: int, body: EntryUpdate, staff_id: int, key: str | None) -> CommandResponse:
    cmd = await dispatch_command(
        session=session, command_type="list.item.update", aggregate_type="list", aggregate_id=str(list_id),
        payload={"list_id": list_id, "item_id": item_id, **body.model_dump(exclude_none=True)},
        requested_by_staff_id=staff_id, idempotency_key=key)
    return _response(cmd, key)


async def delete_entry(session: AsyncSession, list_id: int, item_id: int, staff_id: int, key: str | None) -> CommandResponse:
    cmd = await dispatch_command(
        session=session, command_type="list.item.delete", aggregate_type="list", aggregate_id=str(list_id),
        payload={"list_id": list_id, "item_id": item_id}, requested_by_staff_id=staff_id, idempotency_key=key)
    return _response(cmd, key)
