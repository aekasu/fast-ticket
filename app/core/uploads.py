"""Files sent as part of a command body (a reply or note attachment), as size-capped base64.

The inbox carries JSON only, so a file travels inside the command payload. The caps keep a payload — and the
request that carries it — bounded: ``ATTACHMENT_MAX_FILE_BYTES`` per file, ``ATTACHMENT_MAX_FILES`` per request and
``ATTACHMENT_MAX_TOTAL_BYTES`` together (all counted after decoding). osTicket's own limit (``max_file_size``, an
admin setting) is applied again by the bridge, with osTicket's own refusal. The bridge overwrites the file contents
in the stored payload once the command finishes (the key ``file_data`` is on the same scrub-and-mask list as
passwords: ``app/core/redaction.py``).
"""

import base64
import binascii
import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.config import settings

_MEDIA_TYPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,126}/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,126}$")


def decoded_size(data: str) -> int:
    """The size in bytes of a base64 string once decoded; raises ValueError if it is not valid base64."""
    try:
        return len(base64.b64decode(data, validate=True))
    except (binascii.Error, ValueError) as exc:
        raise ValueError("data must be base64") from exc


class AttachmentUpload(BaseModel):
    name: str = Field(min_length=1, max_length=255, description="File name as it should appear on the thread")
    content_type: Optional[str] = Field(default=None, description="Media type, e.g. image/png; osTicket detects it if absent")
    data: str = Field(min_length=1, description="The file's bytes, base64 encoded")

    @field_validator("name")
    @classmethod
    def _plain_name(cls, value: str) -> str:
        if any(ch in value for ch in "/\\\x00") or value in (".", ".."):
            raise ValueError("a file name has no path")
        return value

    @field_validator("content_type")
    @classmethod
    def _media_type(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not _MEDIA_TYPE.match(value):
            raise ValueError("not a media type")
        return value

    @field_validator("data")
    @classmethod
    def _within_the_file_cap(cls, value: str) -> str:
        if len(value) > settings.ATTACHMENT_MAX_FILE_BYTES * 4 // 3 + 8:   # cheap check before decoding
            raise ValueError(f"file is larger than {settings.ATTACHMENT_MAX_FILE_BYTES} bytes")
        if decoded_size(value) > settings.ATTACHMENT_MAX_FILE_BYTES:
            raise ValueError(f"file is larger than {settings.ATTACHMENT_MAX_FILE_BYTES} bytes")
        return value


class WithAttachments(BaseModel):
    """A command body that may carry files."""

    attachments: list[AttachmentUpload] = Field(
        default_factory=list, description="Files to attach (size-capped, see GET /settings/attachments)")

    @model_validator(mode="after")
    def _within_the_request_caps(self):
        if len(self.attachments) > settings.ATTACHMENT_MAX_FILES:
            raise ValueError(f"at most {settings.ATTACHMENT_MAX_FILES} files per request")
        if sum(decoded_size(a.data) for a in self.attachments) > settings.ATTACHMENT_MAX_TOTAL_BYTES:
            raise ValueError(f"files together are larger than {settings.ATTACHMENT_MAX_TOTAL_BYTES} bytes")
        return self

    def command_payload(self) -> dict:
        """The body as a command payload: the files under ``attachments`` with their bytes in ``file_data``."""
        payload = self.model_dump(exclude={"attachments"})
        if self.attachments:
            payload["attachments"] = [
                {"name": a.name, "type": a.content_type or "", "file_data": a.data} for a in self.attachments]
        return payload


def upload_request_limit() -> int:
    """The request-body limit for a route that accepts files: base64 grows the files by a third, plus the JSON around."""
    return settings.ATTACHMENT_MAX_TOTAL_BYTES * 4 // 3 + settings.MAX_REQUEST_BYTES
