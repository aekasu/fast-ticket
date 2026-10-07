import json
from datetime import time, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.integration.osticket import read_repository as repo
from app.integration.osticket.bridge_read import bridge_json
from app.integration.osticket.commands_service import dispatch_command
from app.modules.schedules.models import (
    CommandResponse, EntryCreate, EntryUpdate, ScheduleCreate, ScheduleDetail, ScheduleEntry, ScheduleItem, ScheduleList, ScheduleUpdate)

_BIZHRS = 0x1


def _item(row: dict) -> dict:
    return dict(id=row["id"], name=row["name"], type="business_hours" if int(row["flags"] or 0) & _BIZHRS else "holidays",
                timezone=row["timezone"] or None, description=row["description"] or "", entry_count=int(row["entry_count"] or 0),
                created=row["created"], updated=row["updated"])


def _clock(value, default: str) -> str:
    """A TIME column as HH:MM:SS (MySQL drivers return a timedelta)."""
    if value is None or value == "":
        return default
    if isinstance(value, timedelta):
        seconds = int(value.total_seconds())
        return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
    if isinstance(value, time):
        return value.strftime("%H:%M:%S")
    text = str(value)
    return text if len(text.split(":")[0]) == 2 else f"0{text}"


def _entry(row: dict, descriptions: dict[int, str]) -> ScheduleEntry:
    starts_at, ends_at = _clock(row["starts_at"], "00:00:00"), _clock(row["ends_at"], "23:59:59")
    return ScheduleEntry(
        id=row["id"], name=row["name"], repeats=row["repeats"], starts_on=row["starts_on"], starts_at=starts_at, ends_at=ends_at,
        all_day=starts_at == "00:00:00" and ends_at == "23:59:59", stops_on=row["stops_on"].date() if row["stops_on"] else None,
        day=row["day"], week=row["week"], month=row["month"], description=descriptions.get(int(row["id"]), ""))


async def list_schedules(session: AsyncSession, *, page: int, page_size: int) -> ScheduleList:
    rows = await repo.list_schedules(session, limit=page_size, offset=(page - 1) * page_size)
    return ScheduleList(items=[ScheduleItem(**_item(dict(r))) for r in rows], total=await repo.count_schedules(session),
                        page=page, page_size=page_size)


async def get_schedule(session: AsyncSession, schedule_id: int, staff_id: int) -> ScheduleDetail | None:
    row = await repo.get_schedule(session, schedule_id)
    if row is None:
        return None
    entries = [dict(e) for e in await repo.list_schedule_entries(session, schedule_id)]
    descriptions = {}
    if entries:
        described = await bridge_json("schedule.describe", staff_id, {"schedule_id": schedule_id})
        descriptions = {int(k): v for k, v in described["descriptions"].items()}
    try:
        holidays = json.loads(row["configuration"] or "{}").get("holidays") or []
    except ValueError:
        holidays = []
    return ScheduleDetail(**_item(row), holiday_schedule_ids=[int(h) for h in holidays],
                          entries=[_entry(e, descriptions) for e in entries])


def _response(cmd, key) -> CommandResponse:
    return CommandResponse(command_id=cmd.id, status=cmd.status, idempotency_key=key)


def _entry_payload(body) -> dict:
    data = body.model_dump(mode="json", exclude_none=True)
    return data


async def create_schedule(session: AsyncSession, body: ScheduleCreate, staff_id: int, key: str | None) -> CommandResponse:
    cmd = await dispatch_command(
        session=session, command_type="schedule.create", aggregate_type="schedule", aggregate_id="",
        payload=body.model_dump(exclude_none=True), requested_by_staff_id=staff_id, idempotency_key=key)
    return _response(cmd, key)


async def update_schedule(session: AsyncSession, schedule_id: int, body: ScheduleUpdate, staff_id: int, key: str | None) -> CommandResponse:
    cmd = await dispatch_command(
        session=session, command_type="schedule.update", aggregate_type="schedule", aggregate_id=str(schedule_id),
        payload={"schedule_id": schedule_id, **body.model_dump(exclude_none=True)}, requested_by_staff_id=staff_id, idempotency_key=key)
    return _response(cmd, key)


async def delete_schedule(session: AsyncSession, schedule_id: int, staff_id: int, key: str | None) -> CommandResponse:
    cmd = await dispatch_command(
        session=session, command_type="schedule.delete", aggregate_type="schedule", aggregate_id=str(schedule_id),
        payload={"schedule_id": schedule_id}, requested_by_staff_id=staff_id, idempotency_key=key)
    return _response(cmd, key)


async def create_entry(session: AsyncSession, schedule_id: int, body: EntryCreate, staff_id: int, key: str | None) -> CommandResponse:
    cmd = await dispatch_command(
        session=session, command_type="schedule.entry.create", aggregate_type="schedule", aggregate_id=str(schedule_id),
        payload={"schedule_id": schedule_id, **_entry_payload(body)}, requested_by_staff_id=staff_id, idempotency_key=key)
    return _response(cmd, key)


async def update_entry(session: AsyncSession, schedule_id: int, entry_id: int, body: EntryUpdate, staff_id: int, key: str | None) -> CommandResponse:
    cmd = await dispatch_command(
        session=session, command_type="schedule.entry.update", aggregate_type="schedule", aggregate_id=str(schedule_id),
        payload={"schedule_id": schedule_id, "entry_id": entry_id, **_entry_payload(body)}, requested_by_staff_id=staff_id, idempotency_key=key)
    return _response(cmd, key)


async def delete_entry(session: AsyncSession, schedule_id: int, entry_id: int, staff_id: int, key: str | None) -> CommandResponse:
    cmd = await dispatch_command(
        session=session, command_type="schedule.entry.delete", aggregate_type="schedule", aggregate_id=str(schedule_id),
        payload={"schedule_id": schedule_id, "entry_id": entry_id}, requested_by_staff_id=staff_id, idempotency_key=key)
    return _response(cmd, key)
