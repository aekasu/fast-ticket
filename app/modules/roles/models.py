from typing import Literal

from pydantic import BaseModel, Field


class Role(BaseModel):
    id: int
    name: str
    notes: str = ""
    enabled: bool
    permissions: list[str] = Field(description="Role permission keys switched on for this role")


class RoleListResponse(BaseModel):
    items: list[Role]
    page: int
    page_size: int


class PermissionKey(BaseModel):
    key: str
    kind: Literal["global", "role"] = Field(
        description="global: stored on the staff member; role: stored on their roles, per department")


class PermissionKeyList(BaseModel):
    permissions: list[PermissionKey]
