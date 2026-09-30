"""bridge/schema.php (what the plugin creates) must match the SQLModel classes (what FastAPI uses)."""

import re
from pathlib import Path

from app.integration.osticket.commands import IntegrationInbox
from app.integration.osticket.models import IntegrationOutbox

SCHEMA = (Path(__file__).resolve().parents[2] / "bridge" / "schema.php").read_text()


def _php_columns(table_placeholder: str) -> set[str]:
    block = SCHEMA[SCHEMA.index(f"CREATE TABLE IF NOT EXISTS `{{{table_placeholder}}}`") :]
    block = block[: block.index("ENGINE=")]
    return set(re.findall(r"^\s+`(\w+)`\s+[A-Z]", block, re.M))


def test_inbox_columns_match_the_model():
    assert _php_columns("inbox") == {c.name for c in IntegrationInbox.__table__.columns}


def test_outbox_columns_match_the_model():
    assert _php_columns("outbox") == {c.name for c in IntegrationOutbox.__table__.columns}


def test_no_text_column_has_a_default():
    """MySQL < 8.0.13 rejects DEFAULT on TEXT/BLOB columns."""
    assert not re.search(r"(MEDIUM|LONG)?TEXT\s+(NOT NULL\s+)?DEFAULT", SCHEMA)
