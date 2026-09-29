"""Integration outbox model — events emitted by the PHP bridge.

The PHP plugin writes rows here after executing each command.  The Python
outbox worker polls this table, dispatches to registered event handlers, and
broadcasts updates over WebSocket.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime
from sqlmodel import Field, SQLModel

# Timestamps are naive UTC (osTicket/MySQL DATETIME, PHP writes NOW()). An explicit
# sa_type bypasses sqlmodel>=0.0.47's wrapper, which rejects naive datetimes.
_TS = DateTime(timezone=False)


class IntegrationOutbox(SQLModel, table=True):
    """ost_integration_outbox — events produced by PHP-side command execution."""

    __tablename__ = "ost_integration_outbox"

    id: Optional[int] = Field(default=None, primary_key=True)
    event_type: str = Field(index=True)                     # e.g. "ticket.created"
    aggregate_type: str = Field(index=True)                 # e.g. "ticket"
    aggregate_id: str = Field(default="", index=True)
    payload: str = Field(default="{}")                      # JSON-serialised event data
    status: str = Field(default="pending", index=True)      # pending | processing | processed | failed
    retry_count: int = Field(default=0)
    error_message: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow, sa_type=_TS)
    locked_at: Optional[datetime] = Field(default=None, sa_type=_TS)
    processed_at: Optional[datetime] = Field(default=None, sa_type=_TS)
