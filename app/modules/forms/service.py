"""Forms service layer."""

import json
import logging
from sqlalchemy.ext.asyncio import AsyncSession

from app.integration.osticket import read_repository as repo
from app.integration.osticket.commands_service import dispatch_command
from app.modules.forms.models import (
    CommandResponse,
    FormCreate,
    FormUpdate,
    FormListItem,
    FieldConfiguration,
    FormFieldCreate,
    FormFieldItem,
    FormFieldUpdate,
    FormDetail,
    FormListResponse,
)

logger = logging.getLogger(__name__)


def _cmd_resp(cmd, idempotency_key: str | None) -> CommandResponse:
    return CommandResponse(
        command_id=cmd.id,
        status=cmd.status,
        idempotency_key=idempotency_key,
    )


# DynamicFormField flag bits (include/class.dynamic_forms.php)
_ENABLED, _MASK_CHANGE, _MASK_DELETE, _MASK_NAME = 0x1, 0x10, 0x20, 0x40000
_CV, _CE, _CR, _AV, _AE, _AR = 0x100, 0x200, 0x400, 0x1000, 0x2000, 0x4000
_MODE_BITS = _CV | _CE | _CR | _AV | _AE | _AR
# allRequirementModes(): mode -> the view / edit / required bits it sets
FIELD_MODES = {
    "optional": _CV | _AV | _CE | _AE,
    "required": _CV | _AV | _CE | _AE | _CR | _AR,
    "required_for_users": _CV | _AV | _CE | _AE | _CR,
    "required_for_agents": _CV | _AV | _CE | _AE | _AR,
    "internal_optional": _AV | _AE,
    "internal_required": _AV | _AE | _AR,
    "users_only": _CV | _CE | _CR,
}
_MODE_BY_BITS = {bits: mode for mode, bits in FIELD_MODES.items()}


def _field_item(row: dict) -> FormFieldItem:
    flags = int(row.get("flags") or 0)
    try:
        stored = json.loads(row.get("configuration") or "{}")
    except (TypeError, ValueError):
        stored = {}
    stored = stored if isinstance(stored, dict) else {}
    stored["validator_error"] = stored.pop("validator-error", stored.get("validator_error"))
    known = {k: v for k, v in stored.items() if k in FieldConfiguration.model_fields and v is not None}
    try:
        configuration = FieldConfiguration(**known)
    except ValueError:   # a value osTicket allows but this model does not describe: show what parses
        configuration = FieldConfiguration()
    return FormFieldItem(
        id=row["id"], name=row["name"], label=row["label"], type=row["type"],
        is_required=bool(row["is_required"]), sort=int(row.get("sort") or 0), hint=row.get("hint") or "",
        mode=_MODE_BY_BITS.get(flags & _MODE_BITS), is_enabled=bool(flags & _ENABLED),
        is_deletable=not flags & _MASK_DELETE, is_type_locked=bool(flags & _MASK_CHANGE),
        is_name_locked=bool(flags & _MASK_NAME), configuration=configuration,
    )


# ── Forms ─────────────────────────────────────────────────────────────────────

async def list_forms(
    session: AsyncSession,
    page: int = 1,
    page_size: int = 50,
) -> FormListResponse:
    page_size = min(page_size, 200)
    offset = (page - 1) * page_size
    rows = await repo.list_forms(session, limit=page_size, offset=offset)
    
    items = [
        FormListItem(
            id=row["id"],
            title=row["title"],
            type=row["type"],
            is_deletable=bool(row["is_deletable"]),
            created=row["created"],
            updated=row["updated"],
        )
        for row in rows
    ]
    
    return FormListResponse(
        items=items,
        page=page,
        page_size=page_size,
    )

async def get_form(session: AsyncSession, form_id: int) -> FormDetail | None:
    row = await repo.get_form(session, form_id)
    if not row:
        return None
        
    field_rows = await repo.get_form_fields(session, form_id)
    fields = [_field_item(dict(f)) for f in field_rows]
    
    return FormDetail(
        **row,
        fields=fields,
    )


async def create_form(
    session: AsyncSession,
    body: FormCreate,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="form.create",
        aggregate_type="form",
        aggregate_id="",
        payload=body.model_dump(exclude_none=True),
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)

async def update_form(
    session: AsyncSession,
    form_id: int,
    body: FormUpdate,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="form.update",
        aggregate_type="form",
        aggregate_id=str(form_id),
        payload={"form_id": form_id, **body.model_dump(exclude_none=True)},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)

async def delete_form(
    session: AsyncSession,
    form_id: int,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="form.delete",
        aggregate_type="form",
        aggregate_id=str(form_id),
        payload={"form_id": form_id},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)


def _payload(body) -> dict:
    data = body.model_dump(exclude_none=True)
    config = data.get("configuration")
    if config is not None:
        # osTicket's key is 'validator-error'
        if "validator_error" in config:
            config["validator-error"] = config.pop("validator_error")
    return data


async def add_field(session, form_id, body: FormFieldCreate, staff_id, idempotency_key):
    cmd = await dispatch_command(
        session=session,
        command_type="form.field.add",
        aggregate_type="form",
        aggregate_id=str(form_id),
        payload={"form_id": form_id, **_payload(body)},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)


async def update_field(session, form_id, field_id, body: FormFieldUpdate, staff_id, idempotency_key):
    cmd = await dispatch_command(
        session=session,
        command_type="form.field.update",
        aggregate_type="form",
        aggregate_id=str(form_id),
        payload={"form_id": form_id, "field_id": field_id, **_payload(body)},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)


async def delete_field(session, form_id, field_id, delete_data: bool, staff_id, idempotency_key):
    cmd = await dispatch_command(
        session=session,
        command_type="form.field.delete",
        aggregate_type="form",
        aggregate_id=str(form_id),
        payload={"form_id": form_id, "field_id": field_id, "delete_data": delete_data},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)
