"""User and Organization domain Pydantic models."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.modules.tickets.models import CommandResponse

__all__ = ["CommandResponse"]

# ── Organizations ──────────────────────────────────────────────────────────────

class OrganizationListItem(BaseModel):
    id: int
    name: str
    created: datetime
    updated: datetime

class OrganizationListResponse(BaseModel):
    items: list[OrganizationListItem]
    total: Optional[int] = None
    page: int
    page_size: int

class OrganizationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    address: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    internal_notes: Optional[str] = None

class OrganizationUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    address: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    internal_notes: Optional[str] = None

class OrganizationDetail(OrganizationListItem):
    address: str = ""
    phone: str = ""
    website: str = ""
    internal_notes: str = ""


# ── Users ──────────────────────────────────────────────────────────────────────

class UserListItem(BaseModel):
    id: int
    name: str
    email: str
    org_id: Optional[int] = None
    org_name: Optional[str] = None
    status: int = 0
    created: datetime
    updated: datetime

class UserListResponse(BaseModel):
    items: list[UserListItem]
    total: Optional[int] = None
    page: int
    page_size: int

class UserDetail(UserListItem):
    phone: str = ""
    mobile: str = ""
    internal_notes: str = ""

class UserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    email: str = Field(min_length=3, max_length=255)
    org_id: Optional[int] = None
    phone: Optional[str] = None
    mobile: Optional[str] = None
    internal_notes: Optional[str] = None

class UserUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    email: Optional[str] = Field(default=None, min_length=3, max_length=255)
    org_id: Optional[int] = None
    phone: Optional[str] = None
    mobile: Optional[str] = None
    internal_notes: Optional[str] = None
