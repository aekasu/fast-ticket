from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.modules.tickets.models import CommandResponse

__all__ = ["CommandResponse"]

SignatureType = Literal["none", "mine", "dept"]
PaperSize = Literal["Letter", "Legal", "Ledger", "A4", "A3"]


class Profile(BaseModel):
    """What osTicket's "My Profile" page (``scp/profile.php``) holds for the signed-in agent."""

    username: str
    firstname: str
    lastname: str
    email: str
    phone: str = ""
    phone_ext: str = ""
    mobile: str = ""
    signature: str = ""
    timezone: str = Field(default="", description="empty = the system's")
    lang: str = Field(default="", description="empty = the system's")
    max_page_size: int = Field(description="0 = the system's")
    auto_refresh_rate: int = Field(description="minutes; 0 = disabled")
    default_signature_type: SignatureType
    default_paper_size: PaperSize
    onvacation: bool
    # stored per agent in osTicket's config table; empty / absent = the system default
    datetime_format: str = Field(default="", description="relative | empty")
    default_from_name: str = Field(default="", description="email | dept | mine | empty")
    thread_view_order: str = Field(default="", description="desc | asc | empty")
    default_ticket_queue_id: int = 0
    reply_redirect: str = Field(default="Ticket", description="Queue | Ticket")
    img_att_view: str = Field(default="download", description="inline | download")
    editor_spacing: str = Field(default="single", description="single | double")


class ProfileUpdate(BaseModel):
    """Partial: what is absent keeps its stored value (osTicket's page rewrites every field)."""

    firstname: Optional[str] = Field(default=None, min_length=1, max_length=32)
    lastname: Optional[str] = Field(default=None, min_length=1, max_length=32)
    email: Optional[str] = Field(default=None, min_length=3, max_length=255, pattern=r"^[^@\s]+@[^@\s]+$")
    phone: Optional[str] = Field(default=None, max_length=24)
    phone_ext: Optional[str] = Field(default=None, max_length=6)
    mobile: Optional[str] = Field(default=None, max_length=24)
    signature: Optional[str] = Field(default=None, max_length=10000)
    timezone: Optional[str] = Field(default=None, max_length=64)
    lang: Optional[str] = Field(default=None, max_length=16)
    max_page_size: Optional[int] = Field(default=None, ge=0, le=50)
    auto_refresh_rate: Optional[int] = Field(default=None, ge=0, le=30)
    default_signature_type: Optional[SignatureType] = None
    default_paper_size: Optional[PaperSize] = None
    onvacation: Optional[bool] = None
    datetime_format: Optional[Literal["relative", ""]] = None
    default_from_name: Optional[Literal["email", "dept", "mine", ""]] = None
    thread_view_order: Optional[Literal["desc", "asc", ""]] = None
    default_ticket_queue_id: Optional[int] = Field(default=None, ge=0)
    reply_redirect: Optional[Literal["Queue", "Ticket"]] = None
    img_att_view: Optional[Literal["inline", "download"]] = None
    editor_spacing: Optional[Literal["single", "double"]] = None


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=1, max_length=128, description="osTicket's password policy decides what is acceptable")
