"""Timestamps: osTicket stores naive datetimes, the API answers unambiguous instants.

osTicket writes ``NOW()`` and friends into DATETIME columns in **the database server's timezone** and
converts on display (``db_timezone``). A bare ``"2026-10-03T18:18:02"`` therefore means nothing to a client
that does not know that zone. Every timestamp the API *returns* is an ISO 8601 instant in UTC with a ``Z``
(``"2026-10-03T18:18:02Z"``): response models declare their fields as ``Instant`` (a naive value is read in the
database's timezone) or ``UtcInstant`` (a value this API wrote itself, always UTC). A datetime a client
*sends* is declared ``UtcInput``: with an offset it is converted to UTC, without one it is taken as UTC.

The database timezone is found once at startup (``load_database_timezone``): ``DB_TIMEZONE`` if set, else
osTicket's own persisted ``db_timezone`` setting, else the server's ``time_zone`` / ``system_time_zone`` —
the same sources osTicket uses. Until then, or if none can be understood, it is UTC.
"""

import logging
import re
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Annotated
from zoneinfo import ZoneInfo

from pydantic import AfterValidator
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_database_tz: tzinfo = timezone.utc
_OFFSET = re.compile(r"^([+-])(\d{1,2}):(\d{2})$")


def parse_timezone(name: str | None) -> tzinfo | None:
    """A MySQL/IANA timezone name or ``+HH:MM`` offset as a tzinfo; None if it is not understood."""
    name = (name or "").strip()
    if not name:
        return None
    if name.upper() in ("UTC", "Z", "+00:00", "-00:00"):
        return timezone.utc
    if name == "GMT":   # PHP reads GMT as UTC, MySQL as Europe/London; osTicket takes London
        return ZoneInfo("Europe/London")
    match = _OFFSET.match(name)
    if match:
        delta = timedelta(hours=int(match[2]), minutes=int(match[3]))
        return timezone(-delta if match[1] == "-" else delta)
    try:
        return ZoneInfo(name)
    except Exception:
        return None


def database_timezone() -> tzinfo:
    return _database_tz


def set_database_timezone(tz: tzinfo) -> None:
    global _database_tz
    _database_tz = tz


def resolve_database_timezone(override: str, persisted: str | None, server: str | None, system: str | None) -> tuple[tzinfo, str]:
    """The timezone naive osTicket datetimes are in, and where it came from (for the log and ``make doctor``)."""
    for source, value in (("DB_TIMEZONE", override), ("osTicket db_timezone", persisted)):
        tz = parse_timezone(value)
        if tz is not None:
            return tz, f"{source}={value}"
    name = system if (server or "SYSTEM") == "SYSTEM" else server
    tz = parse_timezone(name)
    if tz is not None:
        return tz, f"database time_zone={name}"
    return timezone.utc, f"UTC (could not read {name!r}; set DB_TIMEZONE)"


async def load_database_timezone(session: AsyncSession, override: str = "") -> str:
    """Find and set the database timezone; returns where it came from."""
    from app.integration.osticket import read_repository as repo

    row = await repo.get_database_timezone(session)
    tz, source = resolve_database_timezone(override, row.get("persisted"), row.get("server"), row.get("system"))
    set_database_timezone(tz)
    logger.info("Database timezone for osTicket timestamps: %s (%s)", tz, source)
    return source


def to_utc(value: datetime, naive_is: tzinfo) -> datetime:
    return (value if value.tzinfo else value.replace(tzinfo=naive_is)).astimezone(timezone.utc)


def _from_database(value: datetime) -> datetime:
    return to_utc(value, _database_tz)


def _from_utc(value: datetime) -> datetime:
    return to_utc(value, timezone.utc)


def _input_to_naive_utc(value: datetime) -> datetime:
    return to_utc(value, timezone.utc).replace(tzinfo=None)


Instant = Annotated[datetime, AfterValidator(_from_database)]
"""A timestamp osTicket (or the bridge's SQL) wrote: naive in the database's timezone, serialised as UTC with Z."""

UtcInstant = Annotated[datetime, AfterValidator(_from_utc)]
"""A timestamp this API wrote itself (always UTC), serialised with Z."""

UtcInput = Annotated[datetime, AfterValidator(_input_to_naive_utc)]
"""A datetime a client sends: converted to UTC if it carries an offset, taken as UTC if it does not."""
