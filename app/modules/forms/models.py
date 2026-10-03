"""Forms domain Pydantic models."""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

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

FieldMode = Literal[
    "optional", "required", "required_for_users", "required_for_agents",
    "internal_optional", "internal_required", "users_only",
]
FIELD_TYPE_PATTERN = (
    r"^(text|memo|thread|datetime|timezone|phone|bool|choices|files|break|info"
    r"|priority|department|assignee|state|flags|list-\d+)$"
)


class FieldConfiguration(BaseModel):
    """Type-specific settings of a form field (osTicket's field configuration pop-up).

    Only the keys that apply to the field's type are accepted — the bridge refuses the rest.
    text: size, length, validator (+ regex), validator_error, placeholder · memo: cols, rows, length, html,
    placeholder · bool: desc · choices: choices (one ``key:Label`` per line), default, prompt, multiselect ·
    datetime: time, gmt, future · phone: ext, digits, format · info: content ·
    list-N (custom list): multiselect, widget, prompt, validator_error.
    """

    model_config = ConfigDict(extra="forbid")

    size: Optional[int] = Field(default=None, ge=1, le=500)
    length: Optional[int] = Field(default=None, ge=0, le=100000)
    validator: Optional[Literal["", "email", "phone", "ip", "number", "regex"]] = None
    regex: Optional[str] = Field(default=None, max_length=255)
    validator_error: Optional[str] = Field(default=None, max_length=255)
    placeholder: Optional[str] = Field(default=None, max_length=255)
    cols: Optional[int] = Field(default=None, ge=1, le=200)
    rows: Optional[int] = Field(default=None, ge=1, le=100)
    html: Optional[bool] = None
    desc: Optional[str] = Field(default=None, max_length=255)
    choices: Optional[str] = Field(default=None, max_length=10000)
    default: Optional[str] = Field(default=None, max_length=255)
    prompt: Optional[str] = Field(default=None, max_length=255)
    multiselect: Optional[bool] = None
    time: Optional[bool] = None
    gmt: Optional[bool] = None
    future: Optional[bool] = None
    ext: Optional[bool] = None
    digits: Optional[int] = Field(default=None, ge=1, le=30)
    format: Optional[Literal["us", ""]] = None
    content: Optional[str] = Field(default=None, max_length=100000)
    widget: Optional[Literal["dropdown", "typeahead"]] = None


class FormFieldItem(BaseModel):
    id: int
    name: str
    label: str
    type: str  # e.g. "text", "choices", "datetime"
    is_required: bool
    sort: int = 0
    hint: str = ""
    mode: Optional[FieldMode] = Field(default=None, description="null when the flags match no standard mode.")
    is_enabled: bool = True
    is_deletable: bool = True
    is_type_locked: bool = False
    is_name_locked: bool = False
    configuration: FieldConfiguration = FieldConfiguration()


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


class FormFieldCreate(BaseModel):
    """Add a field to a form (the Admin Panel's "Add a field")."""

    label: str = Field(min_length=1, max_length=255)
    type: str = Field(pattern=FIELD_TYPE_PATTERN, description="text, memo, choices, bool, datetime, phone, info, break, files, list-<id>, ...")
    name: str = Field(default="", max_length=64, pattern=r"^$|^[A-Za-z_-][A-Za-z0-9_-]*$",
                      description="The field's variable name; letters, digits, _ and -, not starting with a digit. Required for required fields.")
    mode: FieldMode = "optional"
    hint: Optional[str] = Field(default=None, max_length=512)
    sort: Optional[int] = Field(default=None, ge=0)
    configuration: Optional[FieldConfiguration] = None


class FormFieldUpdate(BaseModel):
    """Absent = leave as is. Core fields refuse the changes osTicket locks (type, name, mode, deletion)."""

    label: Optional[str] = Field(default=None, min_length=1, max_length=255)
    type: Optional[str] = Field(default=None, pattern=FIELD_TYPE_PATTERN)
    name: Optional[str] = Field(default=None, max_length=64, pattern=r"^$|^[A-Za-z_-][A-Za-z0-9_-]*$")
    mode: Optional[FieldMode] = None
    hint: Optional[str] = Field(default=None, max_length=512)
    sort: Optional[int] = Field(default=None, ge=0)
    is_enabled: Optional[bool] = None
    configuration: Optional[FieldConfiguration] = Field(
        default=None, description="Merged over the stored configuration: only the keys you send change.")

    @model_validator(mode="after")
    def _something_to_change(self):
        if all(getattr(self, f) is None for f in self.model_fields):
            raise ValueError("give at least one field to change")
        return self
