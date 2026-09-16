"""Integration outbox model — events emitted by the PHP bridge.

The PHP plugin writes rows here after executing each command.  The Python
outbox worker polls this table, dispatches to registered event handlers, and
broadcasts updates over WebSocket.
"""

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


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
    created_at: datetime = Field(default_factory=datetime.utcnow)
    locked_at: Optional[datetime] = Field(default=None)
    processed_at: Optional[datetime] = Field(default=None)
