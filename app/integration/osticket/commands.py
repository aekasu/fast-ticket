"""Integration inbox model — represents queued write commands.

Rows are written by FastAPI and consumed by the PHP bridge plugin via cron.
Never write directly to osTicket's own tables; always go through this table.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime
from sqlmodel import Field, SQLModel

from app.core.config import settings

# Timestamps are naive UTC (osTicket/MySQL DATETIME, PHP writes NOW()). An explicit
# sa_type bypasses sqlmodel>=0.0.47's wrapper, which rejects naive datetimes.
_TS = DateTime(timezone=False)


class IntegrationInbox(SQLModel, table=True):
    """ost_integration_inbox — commands queued by FastAPI for PHP-side execution."""

    __tablename__ = f"{settings.OSTICKET_TABLE_PREFIX}integration_inbox"

    id: Optional[int] = Field(default=None, primary_key=True)
    command_type: str = Field(index=True)                   # e.g. "ticket.create"
    aggregate_type: str = Field(index=True)                 # e.g. "ticket"
    aggregate_id: str = Field(default="", index=True)       # osticket PK as string
    payload: str = Field(default="{}")                      # JSON-serialised payload
    idempotency_key: Optional[str] = Field(default=None, unique=True, index=True)
    status: str = Field(default="pending", index=True)      # pending | processing | done | failed
    requested_by_staff_id: Optional[int] = Field(default=None)
    claimed_by: Optional[str] = Field(default=None)         # PHP claim run id (bridge/plugin.php)
    result_payload: Optional[str] = Field(default=None)     # JSON response from PHP side
    error_message: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow, sa_type=_TS)
    processed_at: Optional[datetime] = Field(default=None, sa_type=_TS)
