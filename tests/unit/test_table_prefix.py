"""osTicket's table prefix is chosen at install time; queries are written for the default `ost_`."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.integration.osticket import read_repository as repo
from app.integration.osticket.commands import IntegrationInbox
from app.integration.osticket.models import IntegrationOutbox


def test_default_prefix_leaves_sql_untouched():
    sql = "SELECT * FROM ost_ticket t JOIN ost_ticket__cdata c ON c.ticket_id = t.ticket_id"
    assert repo.prefixed(sql, "ost_") == sql


def test_a_custom_prefix_replaces_every_table_reference():
    sql = (
        "SELECT t.ticket_id FROM ost_ticket t\n"
        "LEFT JOIN ost_ticket__cdata c ON c.ticket_id = t.ticket_id\n"
        "LEFT JOIN ost_department d ON t.dept_id = d.id"
    )
    out = repo.prefixed(sql, "support_")
    assert "ost_" not in out
    assert "FROM support_ticket t" in out
    assert "JOIN support_ticket__cdata c" in out
    assert "JOIN support_department d" in out


def test_an_empty_prefix_is_allowed():
    assert repo.prefixed("SELECT 1 FROM ost_config", "") == "SELECT 1 FROM config"


def test_a_word_that_merely_ends_in_ost_is_not_rewritten():
    assert repo.prefixed("SELECT almost_done, cost_center FROM ost_ticket", "x_") == (
        "SELECT almost_done, cost_center FROM x_ticket"
    )


def test_every_query_in_the_repository_survives_prefixing():
    """No table reference may be left behind by the rewrite (a column starting `ost_` would break it)."""
    import inspect
    import re

    source = inspect.getsource(repo)
    tables = set(re.findall(r"(?<![A-Za-z0-9_])ost_[a-z][a-z_]*", source))
    assert len(tables) > 15
    assert not any(repo.prefixed(t, "zz_").startswith("ost_") for t in tables)


def test_integration_tables_default_to_the_ost_prefix():
    assert IntegrationInbox.__tablename__ == "ost_integration_inbox"
    assert IntegrationOutbox.__tablename__ == "ost_integration_outbox"


@pytest.mark.parametrize("bad", ["ost-", "ost_;DROP", "a b", "x" * 33])
def test_a_malformed_prefix_is_rejected_at_startup(bad):
    with pytest.raises(ValidationError):
        Settings(
            DATABASE_URL="sqlite+aiosqlite://",
            FASTAPI_INTEGRATION_KEY="k" * 40,
            OSTICKET_TABLE_PREFIX=bad,
        )
