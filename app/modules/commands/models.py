"""Commands domain Pydantic models."""

from typing import Optional

from pydantic import BaseModel

from app.core.timezone import Instant, UtcInstant


class CommandListItem(BaseModel):
    id: int
    command_type: str
    aggregate_type: str
    aggregate_id: str
    status: str
    requested_by_staff_id: Optional[int] = None
    created_at: UtcInstant
    processed_at: Optional[Instant] = None

class CommandListResponse(BaseModel):
    items: list[CommandListItem]
    total: Optional[int] = None
    page: int
    page_size: int

class CommandDetail(CommandListItem):
    payload: str
    idempotency_key: Optional[str] = None
    result_payload: Optional[str] = None
    error_message: Optional[str] = None
