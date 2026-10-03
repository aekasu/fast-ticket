"""User and Organization domain Pydantic models."""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

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

OrgSharing = Literal["disabled", "primary_contacts", "all_members"]


class _OrganizationSettings(BaseModel):
    """What osTicket's organization profile edits besides the form fields. ``0`` for an id means none."""

    manager_staff_id: Optional[int] = Field(default=None, ge=0, description="Account manager (an agent).")
    manager_team_id: Optional[int] = Field(default=None, ge=0, description="Account manager (a team).")
    domain: Optional[str] = Field(
        default=None, max_length=256, description="Comma-separated email domains whose users join automatically.")
    sharing: Optional[OrgSharing] = Field(
        default=None, description="Who sees all of the organization's tickets: nobody extra, primary contacts, or every member.")
    collab_primary_contacts: Optional[bool] = Field(default=None, description="Add primary contacts to every ticket.")
    collab_all_members: Optional[bool] = Field(default=None, description="Add every member to every ticket.")
    assign_account_manager: Optional[bool] = Field(default=None, description="Assign new tickets to the account manager.")
    primary_contact_ids: Optional[list[int]] = Field(
        default=None, description="Members who are primary contacts; replaces the set ([] clears).")

    @model_validator(mode="after")
    def _one_manager(self):
        if self.manager_staff_id and self.manager_team_id:
            raise ValueError("the account manager is an agent or a team, not both")
        return self

    @field_validator("primary_contact_ids")
    @classmethod
    def _contacts_once(cls, value):
        if value is not None and (len(set(value)) != len(value) or any(i <= 0 for i in value)):
            raise ValueError("contact ids must be positive and listed once")
        return value


class OrganizationCreate(_OrganizationSettings):
    name: str = Field(min_length=1, max_length=255)
    address: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    internal_notes: Optional[str] = None

class OrganizationUpdate(_OrganizationSettings):
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
    manager_staff_id: int = 0
    manager_team_id: int = 0
    domain: str = ""
    sharing: OrgSharing = "disabled"
    collab_primary_contacts: bool = False
    collab_all_members: bool = False
    assign_account_manager: bool = False
    primary_contact_ids: list[int] = []


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
