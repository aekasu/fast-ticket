"""Staff, Teams, and Departments domain Pydantic models."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.core.permissions import GLOBAL_PERMISSIONS

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

class DepartmentAccess(BaseModel):
    """Access to a department beyond the agent's primary one, with the role they hold there."""

    dept_id: int = Field(gt=0)
    role_id: int = Field(gt=0)
    alerts: bool = True


class StaffDetail(StaffListItem):
    phone: str = ""
    mobile: str = ""
    phone_ext: str = ""
    notes: str = ""
    teams: list[int] = []  # List of team_ids
    is_visible: bool = True
    on_vacation: bool = False
    assigned_only: bool = False
    assign_use_primary_role: bool = False
    permissions: list[str] = Field(default=[], description="Global permissions held (role permissions come from roles).")
    department_access: list[DepartmentAccess] = []

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
    phone_ext: Optional[str] = Field(default=None, max_length=24)
    notes: Optional[str] = Field(default=None, max_length=10000)
    is_visible: Optional[bool] = Field(default=None, description="Listed in the staff directory.")
    on_vacation: Optional[bool] = None
    assigned_only: Optional[bool] = Field(default=None, description="Sees only tickets assigned to them.")
    assign_use_primary_role: Optional[bool] = Field(
        default=None, description="Use the primary role for tickets assigned to them in other departments.")
    permissions: Optional[list[str]] = Field(
        default=None, description="Global permission keys (see app/core/permissions.py); [] clears them all.")
    department_access: Optional[list[DepartmentAccess]] = Field(
        default=None, description="Extra departments and roles; [] removes them all.")

    @field_validator("permissions")
    @classmethod
    def _known_global_permissions(cls, value):
        if value is None:
            return value
        unknown = sorted(set(value) - GLOBAL_PERMISSIONS)
        if unknown:
            raise ValueError(f"not global osTicket permissions: {unknown} (role permissions belong to roles)")
        return sorted(set(value))

    @field_validator("department_access")
    @classmethod
    def _one_row_per_department(cls, value):
        if value is not None and len({a.dept_id for a in value}) != len(value):
            raise ValueError("a department may appear once")
        return value

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
    phone_ext: Optional[str] = Field(default=None, max_length=24)
    notes: Optional[str] = Field(default=None, max_length=10000)
    is_visible: Optional[bool] = Field(default=None, description="Listed in the staff directory.")
    on_vacation: Optional[bool] = None
    assigned_only: Optional[bool] = Field(default=None, description="Sees only tickets assigned to them.")
    assign_use_primary_role: Optional[bool] = Field(
        default=None, description="Use the primary role for tickets assigned to them in other departments.")
    permissions: Optional[list[str]] = Field(
        default=None, description="Global permission keys (see app/core/permissions.py); [] clears them all.")
    department_access: Optional[list[DepartmentAccess]] = Field(
        default=None, description="Extra departments and roles; [] removes them all.")

    @field_validator("permissions")
    @classmethod
    def _known_global_permissions(cls, value):
        if value is None:
            return value
        unknown = sorted(set(value) - GLOBAL_PERMISSIONS)
        if unknown:
            raise ValueError(f"not global osTicket permissions: {unknown} (role permissions belong to roles)")
        return sorted(set(value))

    @field_validator("department_access")
    @classmethod
    def _one_row_per_department(cls, value):
        if value is not None and len({a.dept_id for a in value}) != len(value):
            raise ValueError("a department may appear once")
        return value
