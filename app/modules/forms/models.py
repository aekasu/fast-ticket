"""Forms domain Pydantic models."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.modules.tickets.models import CommandResponse

__all__ = ["CommandResponse"]

# ── Forms ─────────────────────────────────────────────────────────────────────

class FormListItem(BaseModel):
    id: int
    title: str
    type: str  # e.g. "T" for Ticket, "U" for User, "O" for Organization, "C" for Custom
    is_deletable: bool
    created: datetime
    updated: datetime

class FormListResponse(BaseModel):
    items: list[FormListItem]
    total: Optional[int] = None
    page: int
    page_size: int

class FormFieldItem(BaseModel):
    id: int
    name: str
    label: str
    type: str  # e.g. "text", "choices", "datetime"
    is_required: bool

class FormDetail(FormListItem):
    instructions: str = ""
    notes: str = ""
    fields: list[FormFieldItem] = []

class FormCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    type: str = Field(min_length=1, max_length=16)
    instructions: str = ""
    notes: str = ""

class FormUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    instructions: Optional[str] = None
    notes: Optional[str] = None
