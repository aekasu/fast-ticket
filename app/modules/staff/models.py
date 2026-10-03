"""Staff, Teams, and Departments domain Pydantic models."""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.core.permissions import GLOBAL_PERMISSIONS

from app.modules.tickets.models import CommandResponse

__all__ = ["CommandResponse"]

# ── Departments ───────────────────────────────────────────────────────────────

DepartmentStatus = Literal["active", "disabled", "archived"]
DepartmentAssignment = Literal["all", "members", "primary"]
DepartmentAlerts = Literal["no_one", "admin_only", "dept_only", "dept_and_extended"]

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

class DepartmentMember(BaseModel):
    """Extended access: an agent from another department who may work this one, with their role in it."""

    staff_id: int = Field(gt=0)
    role_id: int = Field(gt=0)
    alerts: bool = True


class DepartmentDetail(DepartmentListItem):
    manager_id: Optional[int] = None
    email_id: Optional[int] = None
    parent_id: Optional[int] = None
    sla_id: Optional[int] = None
    schedule_id: Optional[int] = None
    template_id: Optional[int] = None
    autoresponse_email_id: Optional[int] = None
    signature: str = ""
    status: DepartmentStatus = "active"
    assignment: DepartmentAssignment = "all"
    disable_auto_claim: bool = False
    disable_reopen_auto_assign: bool = False
    alerts_to: DepartmentAlerts = "dept_only"
    ticket_auto_response: bool = True
    message_auto_response: bool = True
    members: list[DepartmentMember] = []


class _DepartmentFields(BaseModel):
    """Everything osTicket's department page edits. ``0`` / ``null`` for an id means none."""

    manager_id: Optional[int] = Field(default=None, ge=0)
    email_id: Optional[int] = Field(default=None, ge=0)
    parent_id: Optional[int] = Field(default=None, ge=0)
    sla_id: Optional[int] = Field(default=None, ge=0)
    schedule_id: Optional[int] = Field(default=None, ge=0)
    template_id: Optional[int] = Field(default=None, ge=0)
    autoresponse_email_id: Optional[int] = Field(default=None, ge=0)
    signature: Optional[str] = Field(default=None, max_length=10000)
    status: Optional[DepartmentStatus] = None
    assignment: Optional[DepartmentAssignment] = Field(
        default=None, description="Who tickets can be assigned to: all agents, department members, or primary members.")
    disable_auto_claim: Optional[bool] = None
    disable_reopen_auto_assign: Optional[bool] = None
    alerts_to: Optional[DepartmentAlerts] = None
    ticket_auto_response: Optional[bool] = None
    message_auto_response: Optional[bool] = None
    members: Optional[list[DepartmentMember]] = Field(
        default=None, description="Extended-access agents; [] removes them all.")

    @field_validator("members")
    @classmethod
    def _one_row_per_agent(cls, value):
        if value is not None and len({m.staff_id for m in value}) != len(value):
            raise ValueError("an agent may appear once")
        return value


class DepartmentCreate(_DepartmentFields):
    name: str = Field(min_length=1, max_length=128)
    is_public: bool = True


class DepartmentUpdate(_DepartmentFields):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    is_public: Optional[bool] = None


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
