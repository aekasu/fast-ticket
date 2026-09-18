"""Forms service layer."""

import logging
from sqlalchemy.ext.asyncio import AsyncSession

from app.integration.osticket import read_repository as repo
from app.integration.osticket.commands_service import dispatch_command
from app.modules.forms.models import (
    CommandResponse,
    FormCreate,
    FormUpdate,
    FormListItem,
    FormFieldItem,
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
    fields = [
        FormFieldItem(
            id=f["id"],
            name=f["name"],
            label=f["label"],
            type=f["type"],
            is_required=bool(f["is_required"]),
        )
        for f in field_rows
    ]
    
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
