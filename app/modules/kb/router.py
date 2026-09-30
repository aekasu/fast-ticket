"""Knowledgebase and Canned Responses endpoints.

Permissions:
  knowledge base reads   any enabled agent (osTicket applies no permission to viewing FAQs)
  knowledge base writes  faq.manage      (global permission)
  canned responses       canned.manage   (role permission — any of the agent's roles)
"""

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_perm, require_staff
from app.modules.kb import service
from app.modules.kb.models import (
    CommandResponse,
    CategoryCreate,
    CategoryUpdate,
    CategoryDetail,
    CategoryListResponse,
    FAQCreate,
    FAQUpdate,
    FAQDetail,
    FAQListResponse,
    CannedCreate,
    CannedUpdate,
    CannedDetail,
    CannedListResponse,
)

router = APIRouter(tags=["kb"])

# ── KB Categories ─────────────────────────────────────────────────────────────

@router.get("/kb/categories", response_model=CategoryListResponse, summary="List KB categories")
async def list_categories(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    current_user: dict = require_staff(),
    session: AsyncSession = Depends(get_session),
):
    return await service.list_categories(session, page=page, page_size=page_size)

@router.get("/kb/categories/{category_id}", response_model=CategoryDetail, summary="Get KB category")
async def get_category(
    category_id: int,
    current_user: dict = require_staff(),
    session: AsyncSession = Depends(get_session),
):
    cat = await service.get_category(session, category_id)
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")
    return cat

@router.post("/kb/categories", response_model=CommandResponse, status_code=202, summary="Create KB category")
async def create_category(
    body: CategoryCreate,
    current_user: dict = require_perm("faq.manage"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.create_category(session, body, current_user["staff_id"], idempotency_key)

@router.patch("/kb/categories/{category_id}", response_model=CommandResponse, status_code=202, summary="Update KB category")
async def update_category(
    category_id: int,
    body: CategoryUpdate,
    current_user: dict = require_perm("faq.manage"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.update_category(session, category_id, body, current_user["staff_id"], idempotency_key)

@router.delete("/kb/categories/{category_id}", response_model=CommandResponse, status_code=202, summary="Delete KB category")
async def delete_category(
    category_id: int,
    current_user: dict = require_perm("faq.manage"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.delete_category(session, category_id, current_user["staff_id"], idempotency_key)


# ── FAQs ──────────────────────────────────────────────────────────────────────

@router.get("/kb/faqs", response_model=FAQListResponse, summary="List FAQs")
async def list_faqs(
    category_id: int | None = Query(default=None, description="Filter by category ID"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    current_user: dict = require_staff(),
    session: AsyncSession = Depends(get_session),
):
    return await service.list_faqs(session, category_id=category_id, page=page, page_size=page_size)

@router.get("/kb/faqs/{faq_id}", response_model=FAQDetail, summary="Get FAQ")
async def get_faq(
    faq_id: int,
    current_user: dict = require_staff(),
    session: AsyncSession = Depends(get_session),
):
    faq = await service.get_faq(session, faq_id)
    if not faq:
        raise HTTPException(status_code=404, detail="FAQ not found")
    return faq

@router.post("/kb/faqs", response_model=CommandResponse, status_code=202, summary="Create FAQ")
async def create_faq(
    body: FAQCreate,
    current_user: dict = require_perm("faq.manage"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.create_faq(session, body, current_user["staff_id"], idempotency_key)

@router.patch("/kb/faqs/{faq_id}", response_model=CommandResponse, status_code=202, summary="Update FAQ")
async def update_faq(
    faq_id: int,
    body: FAQUpdate,
    current_user: dict = require_perm("faq.manage"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.update_faq(session, faq_id, body, current_user["staff_id"], idempotency_key)

@router.delete("/kb/faqs/{faq_id}", response_model=CommandResponse, status_code=202, summary="Delete FAQ")
async def delete_faq(
    faq_id: int,
    current_user: dict = require_perm("faq.manage"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.delete_faq(session, faq_id, current_user["staff_id"], idempotency_key)


# ── Canned Responses ──────────────────────────────────────────────────────────

@router.get("/canned", response_model=CannedListResponse, summary="List canned responses")
async def list_canned(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    current_user: dict = require_perm("canned.manage"),
    session: AsyncSession = Depends(get_session),
):
    return await service.list_canned(session, page=page, page_size=page_size)

@router.get("/canned/{canned_id}", response_model=CannedDetail, summary="Get canned response")
async def get_canned(
    canned_id: int,
    current_user: dict = require_perm("canned.manage"),
    session: AsyncSession = Depends(get_session),
):
    canned = await service.get_canned(session, canned_id)
    if not canned:
        raise HTTPException(status_code=404, detail="Canned response not found")
    return canned

@router.post("/canned", response_model=CommandResponse, status_code=202, summary="Create canned response")
async def create_canned(
    body: CannedCreate,
    current_user: dict = require_perm("canned.manage"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.create_canned(session, body, current_user["staff_id"], idempotency_key)

@router.patch("/canned/{canned_id}", response_model=CommandResponse, status_code=202, summary="Update canned response")
async def update_canned(
    canned_id: int,
    body: CannedUpdate,
    current_user: dict = require_perm("canned.manage"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.update_canned(session, canned_id, body, current_user["staff_id"], idempotency_key)

@router.delete("/canned/{canned_id}", response_model=CommandResponse, status_code=202, summary="Delete canned response")
async def delete_canned(
    canned_id: int,
    current_user: dict = require_perm("canned.manage"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.delete_canned(session, canned_id, current_user["staff_id"], idempotency_key)
