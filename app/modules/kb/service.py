"""Knowledgebase and Canned Responses service layer."""

import logging
from sqlalchemy.ext.asyncio import AsyncSession

from app.integration.osticket import read_repository as repo
from app.integration.osticket.commands_service import dispatch_command
from app.modules.kb.models import (
    CommandResponse,
    CategoryCreate,
    CategoryUpdate,
    CategoryListItem,
    CategoryDetail,
    CategoryListResponse,
    FAQCreate,
    FAQUpdate,
    FAQListItem,
    FAQDetail,
    FAQListResponse,
    CannedCreate,
    CannedUpdate,
    CannedListItem,
    CannedDetail,
    CannedListResponse,
)

logger = logging.getLogger(__name__)


def _cmd_resp(cmd, idempotency_key: str | None) -> CommandResponse:
    return CommandResponse(
        command_id=cmd.id,
        status=cmd.status,
        idempotency_key=idempotency_key,
    )


# ── KB Categories ─────────────────────────────────────────────────────────────

async def list_categories(
    session: AsyncSession,
    page: int = 1,
    page_size: int = 50,
) -> CategoryListResponse:
    page_size = min(page_size, 200)
    offset = (page - 1) * page_size
    rows = await repo.list_categories(session, limit=page_size, offset=offset)
    
    items = [
        CategoryListItem(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            is_public=bool(row["is_public"]),
            created=row["created"],
            updated=row["updated"],
        )
        for row in rows
    ]
    
    return CategoryListResponse(
        items=items,
        page=page,
        page_size=page_size,
    )

async def get_category(session: AsyncSession, category_id: int) -> CategoryDetail | None:
    row = await repo.get_category(session, category_id)
    if not row:
        return None
    return CategoryDetail(**row)


async def create_category(
    session: AsyncSession,
    body: CategoryCreate,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="kb.category.create",
        aggregate_type="kb_category",
        aggregate_id="",
        payload=body.model_dump(exclude_none=True),
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)

async def update_category(
    session: AsyncSession,
    category_id: int,
    body: CategoryUpdate,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="kb.category.update",
        aggregate_type="kb_category",
        aggregate_id=str(category_id),
        payload={"category_id": category_id, **body.model_dump(exclude_none=True)},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)

async def delete_category(
    session: AsyncSession,
    category_id: int,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="kb.category.delete",
        aggregate_type="kb_category",
        aggregate_id=str(category_id),
        payload={"category_id": category_id},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)


# ── FAQs ──────────────────────────────────────────────────────────────────────

async def list_faqs(
    session: AsyncSession,
    category_id: int | None = None,
    page: int = 1,
    page_size: int = 50,
) -> FAQListResponse:
    page_size = min(page_size, 200)
    offset = (page - 1) * page_size
    rows = await repo.list_faqs(session, category_id=category_id, limit=page_size, offset=offset)
    
    items = [
        FAQListItem(
            id=row["id"],
            category_id=row["category_id"],
            question=row["question"],
            is_published=bool(row["is_published"]),
            created=row["created"],
            updated=row["updated"],
        )
        for row in rows
    ]
    
    return FAQListResponse(
        items=items,
        page=page,
        page_size=page_size,
    )

async def get_faq(session: AsyncSession, faq_id: int) -> FAQDetail | None:
    row = await repo.get_faq(session, faq_id)
    if not row:
        return None
    return FAQDetail(**row)


async def create_faq(
    session: AsyncSession,
    body: FAQCreate,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="kb.faq.create",
        aggregate_type="kb_faq",
        aggregate_id="",
        payload=body.model_dump(exclude_none=True),
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)

async def update_faq(
    session: AsyncSession,
    faq_id: int,
    body: FAQUpdate,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="kb.faq.update",
        aggregate_type="kb_faq",
        aggregate_id=str(faq_id),
        payload={"faq_id": faq_id, **body.model_dump(exclude_none=True)},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)

async def delete_faq(
    session: AsyncSession,
    faq_id: int,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="kb.faq.delete",
        aggregate_type="kb_faq",
        aggregate_id=str(faq_id),
        payload={"faq_id": faq_id},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)


# ── Canned Responses ──────────────────────────────────────────────────────────

async def list_canned(
    session: AsyncSession,
    page: int = 1,
    page_size: int = 50,
) -> CannedListResponse:
    page_size = min(page_size, 200)
    offset = (page - 1) * page_size
    rows = await repo.list_canned(session, limit=page_size, offset=offset)
    
    items = [
        CannedListItem(
            id=row["id"],
            title=row["title"],
            is_enabled=bool(row["is_enabled"]),
            dept_id=row["dept_id"],
            created=row["created"],
            updated=row["updated"],
        )
        for row in rows
    ]
    
    return CannedListResponse(
        items=items,
        page=page,
        page_size=page_size,
    )

async def get_canned(session: AsyncSession, canned_id: int) -> CannedDetail | None:
    row = await repo.get_canned(session, canned_id)
    if not row:
        return None
    return CannedDetail(**row)


async def create_canned(
    session: AsyncSession,
    body: CannedCreate,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="kb.canned.create",
        aggregate_type="kb_canned",
        aggregate_id="",
        payload=body.model_dump(exclude_none=True),
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)

async def update_canned(
    session: AsyncSession,
    canned_id: int,
    body: CannedUpdate,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="kb.canned.update",
        aggregate_type="kb_canned",
        aggregate_id=str(canned_id),
        payload={"canned_id": canned_id, **body.model_dump(exclude_none=True)},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)

async def delete_canned(
    session: AsyncSession,
    canned_id: int,
    staff_id: int,
    idempotency_key: str | None,
) -> CommandResponse:
    cmd = await dispatch_command(
        session=session,
        command_type="kb.canned.delete",
        aggregate_type="kb_canned",
        aggregate_id=str(canned_id),
        payload={"canned_id": canned_id},
        requested_by_staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    return _cmd_resp(cmd, idempotency_key)
