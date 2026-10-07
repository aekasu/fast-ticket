from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.core.timezone import Instant, UtcInput
from app.modules.tickets.models import CommandResponse

__all__ = ["CommandResponse"]

LogType = Literal["Debug", "Warning", "Error"]
LogSort = Literal["id", "title", "type", "ip", "created", "updated"]


class LogItem(BaseModel):
    id: int
    type: LogType
    title: str
    logger: str = ""
    ip_address: str = ""
    created: Instant
    updated: Instant


class LogDetail(LogItem):
    log: str = Field(description="The log text osTicket stored")


class LogList(BaseModel):
    items: list[LogItem]
    total: int
    page: int
    page_size: int


class LogDelete(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=200)


class Extension(BaseModel):
    key: str
    name: str
    description: str
    loaded: bool


class PhpSetting(BaseModel):
    name: str
    value: str
    ok: bool


class LanguagePack(BaseModel):
    code: str
    name: str
    version: Optional[str] = None


class SystemInfo(BaseModel):
    """What osTicket's Admin Panel "System Information" page shows."""

    osticket_version: str
    web_server: str
    mysql_version: str
    php_version: str
    extensions: list[Extension]
    php_settings: list[PhpSetting]
    schema_name: str
    schema_signature: str
    space_used_mib: float
    attachment_space_mib: float
    database_timezone: str
    interpreted_timezone: str
    language_packs: list[LanguagePack]
