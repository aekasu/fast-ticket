"""Knowledgebase and Canned Responses domain Pydantic models."""

from typing import Optional

from pydantic import BaseModel, Field

from app.core.timezone import Instant
from app.modules.tickets.models import CommandResponse

__all__ = ["CommandResponse"]

# ── KB Categories ─────────────────────────────────────────────────────────────

class CategoryListItem(BaseModel):
    id: int
    name: str
    description: str
    is_public: bool
    parent_id: Optional[int] = Field(default=None, description="The parent category, if this is a sub-category")
    faq_count: int = Field(default=0, description="FAQs in this category and its sub-categories (osTicket's count)")
    created: Instant
    updated: Instant

class CategoryListResponse(BaseModel):
    items: list[CategoryListItem]
    total: Optional[int] = None
    page: int
    page_size: int

class CategoryDetail(CategoryListItem):
    notes: str = ""

class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    is_public: bool = True
    notes: str = ""

class CategoryUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = None
    is_public: Optional[bool] = None
    notes: Optional[str] = None


# ── FAQs ──────────────────────────────────────────────────────────────────────

class FAQListItem(BaseModel):
    id: int
    category_id: int
    question: str
    is_published: bool
    created: Instant
    updated: Instant

class FAQListResponse(BaseModel):
    items: list[FAQListItem]
    total: Optional[int] = None
    page: int
    page_size: int

class FAQDetail(FAQListItem):
    answer: str = ""
    keywords: str = ""
    notes: str = ""

class FAQCreate(BaseModel):
    category_id: int = Field(gt=0)
    question: str = Field(min_length=1, max_length=255)
    answer: str = Field(min_length=1)
    is_published: bool = True
    keywords: str = ""
    notes: str = ""

class FAQUpdate(BaseModel):
    category_id: Optional[int] = Field(default=None, gt=0)
    question: Optional[str] = Field(default=None, min_length=1, max_length=255)
    answer: Optional[str] = Field(default=None, min_length=1)
    is_published: Optional[bool] = None
    keywords: Optional[str] = None
    notes: Optional[str] = None


# ── Canned Responses ──────────────────────────────────────────────────────────

class CannedListItem(BaseModel):
    id: int
    title: str
    is_enabled: bool
    dept_id: int = 0
    created: Instant
    updated: Instant

class CannedListResponse(BaseModel):
    items: list[CannedListItem]
    total: Optional[int] = None
    page: int
    page_size: int

class CannedDetail(CannedListItem):
    response: str = ""
    notes: str = ""

class CannedCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    response: str = Field(min_length=1)
    is_enabled: bool = True
    dept_id: int = 0
    notes: str = ""

class CannedUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    response: Optional[str] = Field(default=None, min_length=1)
    is_enabled: Optional[bool] = None
    dept_id: Optional[int] = None
    notes: Optional[str] = None
