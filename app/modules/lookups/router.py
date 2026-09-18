"""Lookups endpoints.

Permissions:
  topic.manage
  sla.manage
  priority.manage
"""

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_perm
from app.modules.lookups import service
from app.modules.lookups.models import (
    CommandResponse,
    TopicCreate,
    TopicUpdate,
    TopicDetail,
    TopicListResponse,
    SLACreate,
    SLAUpdate,
    SLADetail,
    SLAListResponse,
    PriorityCreate,
    PriorityUpdate,
    PriorityDetail,
    PriorityListResponse,
)

router = APIRouter(tags=["lookups"])

# ── Help Topics ───────────────────────────────────────────────────────────────

@router.get("/topics", response_model=TopicListResponse, summary="List help topics")
async def list_topics(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    current_user: dict = require_perm("topic.manage"),
    session: AsyncSession = Depends(get_session),
):
    return await service.list_topics(session, page=page, page_size=page_size)

@router.get("/topics/{topic_id}", response_model=TopicDetail, summary="Get help topic")
async def get_topic(
    topic_id: int,
    current_user: dict = require_perm("topic.manage"),
    session: AsyncSession = Depends(get_session),
):
    topic = await service.get_topic(session, topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")
    return topic

@router.post("/topics", response_model=CommandResponse, status_code=202, summary="Create help topic")
async def create_topic(
    body: TopicCreate,
    current_user: dict = require_perm("topic.manage"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.create_topic(session, body, current_user["staff_id"], idempotency_key)

@router.patch("/topics/{topic_id}", response_model=CommandResponse, status_code=202, summary="Update help topic")
async def update_topic(
    topic_id: int,
    body: TopicUpdate,
    current_user: dict = require_perm("topic.manage"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.update_topic(session, topic_id, body, current_user["staff_id"], idempotency_key)

@router.delete("/topics/{topic_id}", response_model=CommandResponse, status_code=202, summary="Delete help topic")
async def delete_topic(
    topic_id: int,
    current_user: dict = require_perm("topic.manage"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.delete_topic(session, topic_id, current_user["staff_id"], idempotency_key)


# ── SLAs ──────────────────────────────────────────────────────────────────────

@router.get("/slas", response_model=SLAListResponse, summary="List SLAs")
async def list_slas(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    current_user: dict = require_perm("sla.manage"),
    session: AsyncSession = Depends(get_session),
):
    return await service.list_slas(session, page=page, page_size=page_size)

@router.get("/slas/{sla_id}", response_model=SLADetail, summary="Get SLA")
async def get_sla(
    sla_id: int,
    current_user: dict = require_perm("sla.manage"),
    session: AsyncSession = Depends(get_session),
):
    sla = await service.get_sla(session, sla_id)
    if not sla:
        raise HTTPException(status_code=404, detail="SLA not found")
    return sla

@router.post("/slas", response_model=CommandResponse, status_code=202, summary="Create SLA")
async def create_sla(
    body: SLACreate,
    current_user: dict = require_perm("sla.manage"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.create_sla(session, body, current_user["staff_id"], idempotency_key)

@router.patch("/slas/{sla_id}", response_model=CommandResponse, status_code=202, summary="Update SLA")
async def update_sla(
    sla_id: int,
    body: SLAUpdate,
    current_user: dict = require_perm("sla.manage"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.update_sla(session, sla_id, body, current_user["staff_id"], idempotency_key)

@router.delete("/slas/{sla_id}", response_model=CommandResponse, status_code=202, summary="Delete SLA")
async def delete_sla(
    sla_id: int,
    current_user: dict = require_perm("sla.manage"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.delete_sla(session, sla_id, current_user["staff_id"], idempotency_key)


# ── Priorities ────────────────────────────────────────────────────────────────

@router.get("/priorities", response_model=PriorityListResponse, summary="List priorities")
async def list_priorities(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    current_user: dict = require_perm("priority.manage"),
    session: AsyncSession = Depends(get_session),
):
    return await service.list_priorities(session, page=page, page_size=page_size)

@router.get("/priorities/{priority_id}", response_model=PriorityDetail, summary="Get priority")
async def get_priority(
    priority_id: int,
    current_user: dict = require_perm("priority.manage"),
    session: AsyncSession = Depends(get_session),
):
    priority = await service.get_priority(session, priority_id)
    if not priority:
        raise HTTPException(status_code=404, detail="Priority not found")
    return priority

@router.post("/priorities", response_model=CommandResponse, status_code=202, summary="Create priority")
async def create_priority(
    body: PriorityCreate,
    current_user: dict = require_perm("priority.manage"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.create_priority(session, body, current_user["staff_id"], idempotency_key)

@router.patch("/priorities/{priority_id}", response_model=CommandResponse, status_code=202, summary="Update priority")
async def update_priority(
    priority_id: int,
    body: PriorityUpdate,
    current_user: dict = require_perm("priority.manage"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.update_priority(session, priority_id, body, current_user["staff_id"], idempotency_key)

@router.delete("/priorities/{priority_id}", response_model=CommandResponse, status_code=202, summary="Delete priority")
async def delete_priority(
    priority_id: int,
    current_user: dict = require_perm("priority.manage"),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await service.delete_priority(session, priority_id, current_user["staff_id"], idempotency_key)
