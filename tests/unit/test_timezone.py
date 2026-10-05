"""Timestamps leave the API as UTC instants with a Z; a datetime that comes in is normalised to UTC."""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import BaseModel

from app.core import timezone as tz
from app.core.timezone import Instant, UtcInput, UtcInstant


class Out(BaseModel):
    at: Instant
    mine: UtcInstant
    maybe: Instant | None = None


class In(BaseModel):
    due: UtcInput | None = None


@pytest.fixture(autouse=True)
def _utc_database():
    tz.set_database_timezone(timezone.utc)
    yield
    tz.set_database_timezone(timezone.utc)


def test_a_naive_database_value_is_answered_as_utc_with_z():
    out = Out(at=datetime(2026, 10, 3, 18, 18, 2), mine=datetime(2026, 10, 3, 18, 18, 2))
    assert out.model_dump_json() == '{"at":"2026-10-03T18:18:02Z","mine":"2026-10-03T18:18:02Z","maybe":null}'


def test_a_naive_database_value_is_read_in_the_databases_timezone():
    tz.set_database_timezone(tz.parse_timezone("Europe/Berlin"))      # CEST, +02:00, on 3 October
    out = Out(at=datetime(2026, 10, 3, 20, 0, 0), mine=datetime(2026, 10, 3, 20, 0, 0))
    assert out.at.isoformat() == "2026-10-03T18:00:00+00:00"          # osTicket's own value, converted
    assert out.mine.isoformat() == "2026-10-03T20:00:00+00:00"        # the API's own value was already UTC


def test_winter_and_summer_offsets_differ():
    tz.set_database_timezone(tz.parse_timezone("America/New_York"))
    summer = Out(at=datetime(2026, 7, 1, 12), mine=datetime(2026, 7, 1, 12)).at
    winter = Out(at=datetime(2026, 1, 1, 12), mine=datetime(2026, 1, 1, 12)).at
    assert (summer.hour, winter.hour) == (16, 17)


def test_an_aware_value_is_only_converted():
    aware = datetime(2026, 10, 3, 20, 0, tzinfo=timezone(timedelta(hours=2)))
    assert Out(at=aware, mine=aware).at.isoformat() == "2026-10-03T18:00:00+00:00"


def test_client_input_with_an_offset_becomes_naive_utc_and_without_one_is_taken_as_utc():
    assert In(due="2026-10-10T10:00:00+02:00").due == datetime(2026, 10, 10, 8, 0)
    assert In(due="2026-10-10T10:00:00Z").due == datetime(2026, 10, 10, 10, 0)
    assert In(due="2026-10-10T10:00:00").due == datetime(2026, 10, 10, 10, 0)
    assert In().due is None


@pytest.mark.parametrize("name,expected_hours", [
    ("UTC", 0), ("+00:00", 0), ("+02:00", 2), ("-05:30", -5.5), ("Europe/Berlin", None), ("GMT", None),
])
def test_parse_timezone(name, expected_hours):
    parsed = tz.parse_timezone(name)
    assert parsed is not None
    if expected_hours is not None:
        assert parsed.utcoffset(datetime(2026, 1, 1)) == timedelta(hours=expected_hours)


def test_parse_timezone_refuses_what_it_cannot_read():
    assert tz.parse_timezone("") is None and tz.parse_timezone(None) is None
    assert tz.parse_timezone("CEST") is None          # an ambiguous abbreviation is not guessed


def test_the_source_order_is_override_then_osticket_then_server():
    resolve = tz.resolve_database_timezone
    assert resolve("+03:00", "UTC", "SYSTEM", "UTC")[1].startswith("DB_TIMEZONE")
    assert resolve("", "Europe/Berlin", "SYSTEM", "UTC")[1].startswith("osTicket db_timezone")
    assert resolve("", None, "SYSTEM", "UTC")[1] == "database time_zone=UTC"          # SYSTEM -> system_time_zone
    assert resolve("", None, "+01:00", "UTC")[1] == "database time_zone=+01:00"
    zone, source = resolve("", None, "SYSTEM", "CEST")
    assert zone is timezone.utc and source.startswith("UTC (could not read")         # falls back, and says so
