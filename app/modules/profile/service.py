from sqlalchemy.ext.asyncio import AsyncSession

from app.integration.osticket import read_repository as repo
from app.integration.osticket.commands_service import dispatch_command
from app.modules.profile.models import CommandResponse, PasswordChange, Profile, ProfileUpdate

_CONFIG_DEFAULTS = {
    "datetime_format": "", "default_from_name": "", "thread_view_order": "", "default_ticket_queue_id": 0,
    "reply_redirect": "Ticket", "img_att_view": "download", "editor_spacing": "single",
}


async def get_profile(session: AsyncSession, staff_id: int) -> Profile | None:
    row = await repo.get_staff_profile(session, staff_id)
    if row is None:
        return None
    config = {**_CONFIG_DEFAULTS, **{k: v for k, v in row["config"].items() if k in _CONFIG_DEFAULTS and v is not None}}
    config["default_ticket_queue_id"] = int(config["default_ticket_queue_id"] or 0)
    return Profile(**{k: v for k, v in row["staff"].items()}, **config)


def _response(cmd, idempotency_key) -> CommandResponse:
    return CommandResponse(command_id=cmd.id, status=cmd.status, idempotency_key=idempotency_key)


async def update_profile(session: AsyncSession, body: ProfileUpdate, staff_id: int, idempotency_key: str | None) -> CommandResponse:
    cmd = await dispatch_command(
        session=session, command_type="profile.update", aggregate_type="staff", aggregate_id=str(staff_id),
        payload=body.model_dump(exclude_none=True), requested_by_staff_id=staff_id, idempotency_key=idempotency_key)
    return _response(cmd, idempotency_key)


async def change_password(session: AsyncSession, body: PasswordChange, staff_id: int, idempotency_key: str | None) -> CommandResponse:
    cmd = await dispatch_command(
        session=session, command_type="profile.change_password", aggregate_type="staff", aggregate_id=str(staff_id),
        payload=body.model_dump(), requested_by_staff_id=staff_id, idempotency_key=idempotency_key)
    return _response(cmd, idempotency_key)
