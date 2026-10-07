from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.core.permissions import ROLE_PERMISSIONS
from app.core.timezone import Instant
from app.modules.tickets.models import CommandResponse

__all__ = ["CommandResponse"]


class Role(BaseModel):
    id: int
    name: str
    notes: str = ""
    enabled: bool
    permissions: list[str] = Field(description="Role permission keys switched on for this role")
    created: Optional[Instant] = None
    updated: Optional[Instant] = None
    in_use: Optional[int] = Field(default=None, description="Agents and department-access entries using the role; a role in use cannot be deleted")


class RoleListResponse(BaseModel):
    items: list[Role]
    page: int
    page_size: int


def _known(keys: list[str] | None):
    unknown = sorted(set(keys or []) - ROLE_PERMISSIONS)
    if unknown:
        raise ValueError(f"unknown role permission(s): {', '.join(unknown)}")
    return keys


class RoleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    notes: str = Field(default="", max_length=2000)
    permissions: list[str] = Field(min_length=1, max_length=64, description="osTicket requires at least one permission")
    enabled: bool = True

    _check = field_validator("permissions")(_known)


class RoleUpdate(BaseModel):
    """Partial: what is absent stays as stored (osTicket's page rewrites everything, so the bridge overlays)."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=64)
    notes: Optional[str] = Field(default=None, max_length=2000)
    permissions: Optional[list[str]] = Field(default=None, min_length=1, max_length=64)
    enabled: Optional[bool] = None

    _check = field_validator("permissions")(_known)


class PermissionKey(BaseModel):
    key: str
    kind: Literal["global", "role"] = Field(
        description="global: stored on the staff member; role: stored on their roles, per department")


class PermissionKeyList(BaseModel):
    permissions: list[PermissionKey]
