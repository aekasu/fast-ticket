import json
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.integration.osticket import read_repository as repo
from app.integration.osticket.commands_service import dispatch_command
from app.modules.emailtemplates.models import (
    CommandResponse, MessageBody, MessageTemplate, MessageUpdate, TemplateSet, TemplateSetCreate, TemplateSetDetail,
    TemplateSetList, TemplateSetUpdate)

# osTicket's own names of the messages (EmailTemplateGroup::$all_names); tests compare this file with the PHP source
NAMES: dict[str, dict[str, str]] = json.loads((Path(__file__).parent / "names.json").read_text())


def _set(row: dict) -> dict:
    return dict(id=row["id"], name=row["name"], lang=row["lang"] or "en_US", notes=row["notes"] or "", is_active=bool(row["is_active"]),
                in_use=bool(row["in_use"]), message_count=int(row["message_count"] or 0), created=row["created"], updated=row["updated"])


def _message(code: str, row: dict | None) -> dict:
    info = NAMES.get(code, {"group": "", "name": code, "description": ""})
    return dict(code_name=code, name=info["name"], group=info["group"], description=info["description"], defined=row is not None,
                subject=(row or {}).get("subject") or "", updated=(row or {}).get("updated"))


async def list_sets(session: AsyncSession, *, page: int, page_size: int) -> TemplateSetList:
    rows = await repo.list_template_sets(session, limit=page_size, offset=(page - 1) * page_size)
    return TemplateSetList(items=[TemplateSet(**_set(dict(r))) for r in rows], total=await repo.count_template_sets(session),
                           page=page, page_size=page_size)


async def get_set(session: AsyncSession, set_id: int) -> TemplateSetDetail | None:
    row = await repo.get_template_set(session, set_id)
    if row is None:
        return None
    stored = {r["code_name"]: dict(r) for r in await repo.list_template_messages(session, set_id)}
    codes = list(NAMES) + sorted(set(stored) - set(NAMES))
    return TemplateSetDetail(**_set(row), messages=[MessageTemplate(**_message(c, stored.get(c))) for c in codes])


async def get_message(session: AsyncSession, set_id: int, code_name: str) -> MessageBody | None:
    if await repo.get_template_set(session, set_id) is None or code_name not in NAMES:
        return None
    row = await repo.get_template_message(session, set_id, code_name)
    return MessageBody(**_message(code_name, row), body=(row or {}).get("body") or "")


def _response(cmd, key) -> CommandResponse:
    return CommandResponse(command_id=cmd.id, status=cmd.status, idempotency_key=key)


async def create_set(session: AsyncSession, body: TemplateSetCreate, staff_id: int, key: str | None) -> CommandResponse:
    cmd = await dispatch_command(
        session=session, command_type="emailtemplate.create", aggregate_type="emailtemplate", aggregate_id="",
        payload=body.model_dump(exclude_none=True), requested_by_staff_id=staff_id, idempotency_key=key)
    return _response(cmd, key)


async def update_set(session: AsyncSession, set_id: int, body: TemplateSetUpdate, staff_id: int, key: str | None) -> CommandResponse:
    cmd = await dispatch_command(
        session=session, command_type="emailtemplate.update", aggregate_type="emailtemplate", aggregate_id=str(set_id),
        payload={"set_id": set_id, **body.model_dump(exclude_none=True)}, requested_by_staff_id=staff_id, idempotency_key=key)
    return _response(cmd, key)


async def delete_set(session: AsyncSession, set_id: int, staff_id: int, key: str | None) -> CommandResponse:
    cmd = await dispatch_command(
        session=session, command_type="emailtemplate.delete", aggregate_type="emailtemplate", aggregate_id=str(set_id),
        payload={"set_id": set_id}, requested_by_staff_id=staff_id, idempotency_key=key)
    return _response(cmd, key)


async def update_message(session: AsyncSession, set_id: int, code_name: str, body: MessageUpdate, staff_id: int, key: str | None) -> CommandResponse:
    cmd = await dispatch_command(
        session=session, command_type="emailtemplate.message.update", aggregate_type="emailtemplate", aggregate_id=str(set_id),
        payload={"set_id": set_id, "code_name": code_name, **body.model_dump()}, requested_by_staff_id=staff_id, idempotency_key=key)
    return _response(cmd, key)
