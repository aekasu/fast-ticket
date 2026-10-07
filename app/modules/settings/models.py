from pydantic import BaseModel, Field


class AttachmentLimits(BaseModel):
    """What an upload may be, so a client can refuse a file before sending it."""

    max_file_bytes: int = Field(description="Largest file one request may carry: the smaller of osTicket's and the API's caps")
    osticket_max_file_bytes: int = Field(description="osTicket's own maximum file size (Admin Panel > Settings > Tickets)")
    api_max_file_bytes: int = Field(description="The API's cap per file")
    max_files: int = Field(description="Most files in one request")
    max_total_bytes: int = Field(description="Most bytes of files in one request")


# ── Admin Panel settings pages ────────────────────────────────────────────────

from typing import Optional  # noqa: E402

from pydantic import create_model  # noqa: E402

from app.modules.settings.sections import SECTIONS  # noqa: E402
from app.modules.tickets.models import CommandResponse  # noqa: E402,F401


def api_name(key: str) -> str:
    return key.replace("-", "_")


def _build(section: str):
    read, update = {}, {}
    for key, kind in SECTIONS[section].items():
        name = api_name(key)
        if kind == "int":
            py, default, field = int, 0, Field(default=None, ge=0, le=2147483647)
        elif kind == "str":
            py, default, field = str, "", Field(default=None, max_length=10000)
        elif kind == "list":
            py, default, field = list[str], [], Field(default=None, max_length=50)
        else:
            py, default, field = bool, False, Field(default=None)
        read[name] = (py, default)
        update[name] = (Optional[py], field)
    title = section.title()
    return (create_model(f"{title}Settings", **read), create_model(f"{title}SettingsUpdate", **update))


SECTION_MODELS = {section: _build(section) for section in SECTIONS}
