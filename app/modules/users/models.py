"""User and Organization domain Pydantic models."""

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.config import settings
from app.core.timezone import Instant
from app.core.custom_fields import CustomFieldAnswer, CustomFields

from app.modules.tickets.models import CommandResponse

__all__ = ["CommandResponse"]

# ── Organizations ──────────────────────────────────────────────────────────────

OrgSort = Literal["name", "users", "created", "updated"]
UserSort = Literal["name", "email", "status", "created", "updated"]


class OrganizationListItem(BaseModel):
    id: int
    name: str
    user_count: int = Field(default=0, description="Members (osTicket's Users column)")
    created: Instant
    updated: Instant
    custom_fields: Optional[list[CustomFieldAnswer]] = Field(default=None, description="Only when the request asks `?fields=name,...`")

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
    custom_fields: Optional[CustomFields] = Field(default=None, description="Answers to custom organization-form fields, by variable name.")

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
    custom_fields: list[CustomFieldAnswer] = []


# ── Users ──────────────────────────────────────────────────────────────────────

class UserImport(BaseModel):
    """Import users from CSV text (osTicket's Import dialog). All-or-nothing: the first bad row refuses the lot."""

    csv: str = Field(min_length=1, description="CSV text: a header row naming user-form fields (name, email, ...), "
                                               "or, with ``pasted``, one ``name, email`` per line")
    pasted: bool = Field(default=False, description="Lines of ``name, email`` with no header row (the dialog's pasted box)")

    @field_validator("csv")
    @classmethod
    def _within_the_cap(cls, value: str) -> str:
        if len(value.encode()) > settings.CSV_IMPORT_MAX_BYTES:
            raise ValueError(f"CSV is larger than {settings.CSV_IMPORT_MAX_BYTES} bytes")
        return value


class UserListItem(BaseModel):
    id: int
    name: str
    email: str
    org_id: Optional[int] = None
    org_name: Optional[str] = None
    status: int = 0
    account_status: Literal["guest", "active", "pending", "locked"] = Field(
        default="guest", description="osTicket's directory Status: no account (Guest), Active (Registered), "
        "Locked (Pending Activation) or Locked (Administrative)")
    ticket_count: int = Field(default=0, description="All of the user's tickets, as osTicket's directory counts them")
    created: Instant
    updated: Instant
    custom_fields: Optional[list[CustomFieldAnswer]] = Field(default=None, description="Only when the request asks `?fields=name,...`")

class UserListResponse(BaseModel):
    items: list[UserListItem]
    total: Optional[int] = None
    page: int
    page_size: int

class UserAccountInfo(BaseModel):
    """The end user's login account (never the password)."""

    username: Optional[str] = None
    timezone: str = ""
    status: Literal["active", "pending", "locked"]
    is_confirmed: bool
    is_locked: bool
    require_password_reset: bool
    forbid_password_reset: bool
    registered: Optional[Instant] = None


class UserDetail(UserListItem):
    phone: str = ""
    mobile: str = ""
    internal_notes: str = ""
    is_primary_contact: bool = False
    custom_fields: list[CustomFieldAnswer] = []
    account: Optional[UserAccountInfo] = Field(default=None, description="null until the user registers.")

class UserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    email: str = Field(min_length=3, max_length=255)
    org_id: Optional[int] = None
    phone: Optional[str] = None
    mobile: Optional[str] = None
    internal_notes: Optional[str] = None
    custom_fields: Optional[CustomFields] = Field(default=None, description="Answers to custom user-form fields, by variable name.")

class UserUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    email: Optional[str] = Field(default=None, min_length=3, max_length=255)
    org_id: Optional[int] = None
    phone: Optional[str] = None
    mobile: Optional[str] = None
    internal_notes: Optional[str] = None
    custom_fields: Optional[CustomFields] = Field(default=None, description="Answers to custom user-form fields, by variable name.")


class UserAccountRegister(BaseModel):
    """Register a login account. Either email the user an activation link (they pick the password) or
    set a temporary one (the account is active at once)."""

    send_email: bool = False
    password: Optional[str] = Field(default=None, min_length=1, max_length=128, description="Temporary password; required unless send_email.")
    username: Optional[str] = Field(default=None, max_length=64, description="Optional; users can always sign in with their email.")
    timezone: Optional[str] = Field(default=None, max_length=64)
    require_password_reset: bool = False
    forbid_password_reset: bool = False

    @model_validator(mode="after")
    def _password_or_email(self):
        if not self.send_email and not self.password:
            raise ValueError("give a temporary password, or set send_email to email an activation link")
        if self.username and "@" in self.username:
            raise ValueError("a username cannot be an email address (users can always sign in with it)")
        return self


class UserAccountUpdate(BaseModel):
    """Absent = leave as is. Setting a password also confirms the account."""

    username: Optional[str] = Field(default=None, max_length=64)
    timezone: Optional[str] = Field(default=None, max_length=64)
    password: Optional[str] = Field(default=None, min_length=1, max_length=128)
    is_locked: Optional[bool] = None
    require_password_reset: Optional[bool] = None
    forbid_password_reset: Optional[bool] = None

    @model_validator(mode="after")
    def _something_to_change(self):
        if all(getattr(self, f) is None for f in self.model_fields):
            raise ValueError("give at least one field to change")
        if self.username and "@" in self.username:
            raise ValueError("a username cannot be an email address (users can always sign in with it)")
        return self
