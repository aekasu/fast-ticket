from sqlalchemy.ext.asyncio import AsyncSession

from app.integration.osticket import read_repository as repo
from app.integration.osticket.commands_service import dispatch_command
from app.modules.apikeys.models import ApiKey, ApiKeyCreate, ApiKeyList, ApiKeyUpdate, CommandResponse


def _key(row: dict) -> ApiKey:
    return ApiKey(id=row["id"], key=row["key"], ip_address=row["ip_address"], is_active=bool(row["is_active"]),
                  can_create_tickets=bool(row["can_create_tickets"]), can_exec_cron=bool(row["can_exec_cron"]),
                  notes=row["notes"] or "", created=row["created"], updated=row["updated"])


async def list_api_keys(session: AsyncSession, *, page: int, page_size: int) -> ApiKeyList:
    rows = await repo.list_api_keys(session, limit=page_size, offset=(page - 1) * page_size)
    return ApiKeyList(items=[_key(dict(r)) for r in rows], total=await repo.count_api_keys(session), page=page, page_size=page_size)


async def get_api_key(session: AsyncSession, key_id: int) -> ApiKey | None:
    row = await repo.get_api_key(session, key_id)
    return _key(row) if row else None


def _response(cmd, idempotency_key) -> CommandResponse:
    return CommandResponse(command_id=cmd.id, status=cmd.status, idempotency_key=idempotency_key)


async def create_api_key(session: AsyncSession, body: ApiKeyCreate, staff_id: int, idempotency_key: str | None) -> CommandResponse:
    cmd = await dispatch_command(
        session=session, command_type="apikey.create", aggregate_type="apikey", aggregate_id="",
        payload=body.model_dump(), requested_by_staff_id=staff_id, idempotency_key=idempotency_key)
    return _response(cmd, idempotency_key)


async def update_api_key(session: AsyncSession, key_id: int, body: ApiKeyUpdate, staff_id: int, idempotency_key: str | None) -> CommandResponse:
    cmd = await dispatch_command(
        session=session, command_type="apikey.update", aggregate_type="apikey", aggregate_id=str(key_id),
        payload={"key_id": key_id, **body.model_dump(exclude_none=True)}, requested_by_staff_id=staff_id, idempotency_key=idempotency_key)
    return _response(cmd, idempotency_key)


async def delete_api_key(session: AsyncSession, key_id: int, staff_id: int, idempotency_key: str | None) -> CommandResponse:
    cmd = await dispatch_command(
        session=session, command_type="apikey.delete", aggregate_type="apikey", aggregate_id=str(key_id),
        payload={"key_id": key_id}, requested_by_staff_id=staff_id, idempotency_key=idempotency_key)
    return _response(cmd, idempotency_key)
