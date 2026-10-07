from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.integration.osticket import read_repository as repo
from app.integration.osticket.commands_service import dispatch_command
from app.modules.settings.models import SECTION_MODELS, AttachmentLimits, CommandResponse, api_name
from app.modules.settings.sections import SECTIONS


async def attachment_limits(session: AsyncSession) -> AttachmentLimits:
    osticket = await repo.get_max_file_size(session)
    api = settings.ATTACHMENT_MAX_FILE_BYTES
    return AttachmentLimits(
        max_file_bytes=min(osticket, api) if osticket else api,
        osticket_max_file_bytes=osticket,
        api_max_file_bytes=api,
        max_files=settings.ATTACHMENT_MAX_FILES,
        max_total_bytes=settings.ATTACHMENT_MAX_TOTAL_BYTES,
    )


def _coerce(kind: str, raw):
    if kind == "int":
        try:
            return int(str(raw).strip() or 0)
        except ValueError:
            return 0
    if kind in ("check", "flag"):
        return str(raw or "").strip().lower() in ("1", "on", "true", "yes")
    if kind == "list":
        return [c.strip() for c in str(raw or "").split(",") if c.strip()]
    return "" if raw is None else str(raw)


async def get_section(session: AsyncSession, section: str):
    stored = await repo.get_core_config(session)
    model = SECTION_MODELS[section][0]
    return model(**{api_name(key): _coerce(kind, stored.get(key)) for key, kind in SECTIONS[section].items()})


async def update_section(session: AsyncSession, section: str, body, staff_id: int, idempotency_key: str | None) -> CommandResponse:
    given = body.model_dump(exclude_none=True)
    values = {key: given[api_name(key)] for key in SECTIONS[section] if api_name(key) in given}
    cmd = await dispatch_command(
        session=session, command_type="settings.update", aggregate_type="settings", aggregate_id=section,
        payload={"section": section, "values": values}, requested_by_staff_id=staff_id, idempotency_key=idempotency_key)
    return CommandResponse(command_id=cmd.id, status=cmd.status, idempotency_key=idempotency_key)
