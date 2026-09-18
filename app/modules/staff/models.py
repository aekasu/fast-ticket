"""Staff, Teams, and Departments domain Pydantic models."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.modules.tickets.models import CommandResponse

__all__ = ["CommandResponse"]

# ── Departments ───────────────────────────────────────────────────────────────

class DepartmentListItem(BaseModel):
    id: int
    name: str
    is_public: bool
    created: datetime
    updated: datetime

class DepartmentListResponse(BaseModel):
    items: list[DepartmentListItem]
    total: Optional[int] = None
    page: int
    page_size: int

class DepartmentDetail(DepartmentListItem):
    manager_id: Optional[int] = None
    email_id: Optional[int] = None
    flags: int = 0

class DepartmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    is_public: bool = True
    manager_id: Optional[int] = None
    email_id: Optional[int] = None
    flags: int = 0

class DepartmentUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    is_public: Optional[bool] = None
    manager_id: Optional[int] = None
    email_id: Optional[int] = None
    flags: Optional[int] = None


# ── Teams ─────────────────────────────────────────────────────────────────────

class TeamListItem(BaseModel):
    id: int
    name: str
    is_enabled: bool
    created: datetime
    updated: datetime

class TeamListResponse(BaseModel):
    items: list[TeamListItem]
    total: Optional[int] = None
    page: int
    page_size: int

class TeamDetail(TeamListItem):
    lead_id: Optional[int] = None
    notes: str = ""
    members: list[int] = []  # List of staff_ids

class TeamCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    is_enabled: bool = True
    lead_id: Optional[int] = None
    notes: str = ""
    members: list[int] = []

class TeamUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    is_enabled: Optional[bool] = None
    lead_id: Optional[int] = None
    notes: Optional[str] = None
    members: Optional[list[int]] = None


# ── Staff ─────────────────────────────────────────────────────────────────────

class StaffListItem(BaseModel):
    id: int
    username: str
    firstname: str
    lastname: str
    email: str
    is_active: bool
    is_admin: bool
    dept_id: int
    role_id: int
    created: datetime
    updated: datetime

class StaffListResponse(BaseModel):
    items: list[StaffListItem]
    total: Optional[int] = None
    page: int
    page_size: int

class StaffDetail(StaffListItem):
    phone: str = ""
    mobile: str = ""
    teams: list[int] = []  # List of team_ids

class StaffCreate(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    firstname: str = Field(min_length=1, max_length=64)
    lastname: str = Field(min_length=1, max_length=64)
    email: str = Field(min_length=3, max_length=255)
    dept_id: int
    role_id: int
    password: str = Field(min_length=6)
    is_active: bool = True
    is_admin: bool = False
    phone: Optional[str] = None
    mobile: Optional[str] = None
    teams: list[int] = []

class StaffUpdate(BaseModel):
    username: Optional[str] = Field(default=None, min_length=1, max_length=64)
    firstname: Optional[str] = Field(default=None, min_length=1, max_length=64)
    lastname: Optional[str] = Field(default=None, min_length=1, max_length=64)
    email: Optional[str] = Field(default=None, min_length=3, max_length=255)
    dept_id: Optional[int] = None
    role_id: Optional[int] = None
    password: Optional[str] = Field(default=None, min_length=6)
    is_active: Optional[bool] = None
    is_admin: Optional[bool] = None
    phone: Optional[str] = None
    mobile: Optional[str] = None
    teams: Optional[list[int]] = None
