"""Lookups (Topics, SLAs, Priorities) domain Pydantic models."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.modules.tickets.models import CommandResponse

__all__ = ["CommandResponse"]


# ── Help Topics ───────────────────────────────────────────────────────────────

class TopicListItem(BaseModel):
    id: int
    topic: str
    is_active: bool
    is_public: bool
    created: datetime
    updated: datetime

class TopicListResponse(BaseModel):
    items: list[TopicListItem]
    total: Optional[int] = None
    page: int
    page_size: int

class TopicDetail(TopicListItem):
    dept_id: int = 0
    priority_id: int = 0
    sla_id: int = 0
    page_id: int = 0
    flags: int = 0
    notes: str = ""

class TopicCreate(BaseModel):
    topic: str = Field(min_length=1, max_length=255)
    is_active: bool = True
    is_public: bool = True
    dept_id: int = 0
    priority_id: int = 0
    sla_id: int = 0
    page_id: int = 0
    flags: int = 0
    notes: str = ""

class TopicUpdate(BaseModel):
    topic: Optional[str] = Field(default=None, min_length=1, max_length=255)
    is_active: Optional[bool] = None
    is_public: Optional[bool] = None
    dept_id: Optional[int] = None
    priority_id: Optional[int] = None
    sla_id: Optional[int] = None
    page_id: Optional[int] = None
    flags: Optional[int] = None
    notes: Optional[str] = None


# ── SLAs (Service Level Agreements) ───────────────────────────────────────────

class SLAListItem(BaseModel):
    id: int
    name: str
    grace_period: int  # Hours
    is_active: bool
    created: datetime
    updated: datetime

class SLAListResponse(BaseModel):
    items: list[SLAListItem]
    total: Optional[int] = None
    page: int
    page_size: int

class SLADetail(SLAListItem):
    flags: int = 0
    notes: str = ""

class SLACreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    grace_period: int = Field(ge=0)
    is_active: bool = True
    flags: int = 0
    notes: str = ""

class SLAUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=64)
    grace_period: Optional[int] = Field(default=None, ge=0)
    is_active: Optional[bool] = None
    flags: Optional[int] = None
    notes: Optional[str] = None


# ── Priorities ────────────────────────────────────────────────────────────────

class PriorityListItem(BaseModel):
    id: int
    name: str
    desc: str
    urgency: int
    is_public: bool

class PriorityListResponse(BaseModel):
    items: list[PriorityListItem]
    total: Optional[int] = None
    page: int
    page_size: int

class PriorityDetail(PriorityListItem):
    pass

class PriorityCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    desc: str = ""
    urgency: int = 0
    is_public: bool = True

class PriorityUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=64)
    desc: Optional[str] = None
    urgency: Optional[int] = None
    is_public: Optional[bool] = None
