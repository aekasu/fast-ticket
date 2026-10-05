"""Preflight checks for pointing fast-ticket at an osTicket instance (``make doctor``).

Answers "will this work against *this* osTicket?" without a human reading logs:

  * the database is reachable and the table prefix is right,
  * the bridge plugin's two tables exist and match what this API expects,
  * every read query in ``read_repository`` runs against the real schema (a wrong column or a
    schema difference between osTicket versions shows up here, by name),
  * the plugin answers its handshake (osTicket / plugin versions, prefix agreement),
  * the database user is not over-privileged, and
  * commands are being drained (a pending command that nobody picks up means no worker or cron).

Each check returns a ``Check``; nothing here writes to the database.
"""

import inspect
import re
from dataclasses import dataclass
from typing import Awaitable, Callable

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import AccessContext
from app.core.config import settings
from app.integration.osticket import read_repository as repo
from app.integration.osticket.commands import IntegrationInbox
from app.integration.osticket.models import IntegrationOutbox

# osTicket releases this API's queries and the bridge handlers were verified against.
TESTED_OSTICKET_VERSIONS = ("v1.18",)

OK, WARN, FAIL = "ok", "warn", "fail"

# The caller the doctor pretends to be for scoped reads: nobody, in a department that does not exist.
# The query still runs in full (so a wrong column is found), and matches no rows.
DOCTOR_ACCESS = AccessContext(staff_id=0, dept_ids=frozenset({0}), team_ids=frozenset({0}))


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""


# ── pure helpers (unit-tested) ────────────────────────────────────────────────


def is_tested_version(version: str | None) -> bool:
    return bool(version) and any(version.startswith(v) for v in TESTED_OSTICKET_VERSIONS)


def handshake_url(auth_url: str) -> str:
    """``…/api/auth/integration`` -> ``…/api/fast-ticket/handshake`` (same host and key)."""
    return re.sub(r"/auth/integration/?$", "/fast-ticket/handshake", auth_url)


def read_call_kwargs(func: Callable) -> dict | None:
    """Arguments that make a ``read_repository`` function run harmlessly, or None if unknown.

    List functions get ``limit=1``; ids are 0 (no such row); a search gets a dummy query.
    Only parameters without a default are filled beyond those.
    """
    kwargs: dict = {}
    for name, param in list(inspect.signature(func).parameters.items())[1:]:  # skip session
        if name == "access":
            kwargs[name] = DOCTOR_ACCESS   # a scoped read runs, and matches nothing
        elif name == "include_directory":
            kwargs[name] = True
        elif name == "manage":
            kwargs[name] = True    # check_read_queries also runs the other mode
        elif name == "limit":
            kwargs[name] = 1
        elif name == "offset":
            kwargs[name] = 0
        elif name == "query":
            kwargs[name] = "doctor"
        elif name == "kind":
            kwargs[name] = "ticket"
        elif param.default is not inspect.Parameter.empty:
            continue
        elif name == "id" or name.endswith("_id") or name in ("sid", "rid"):
            kwargs[name] = 0
        else:
            return None
    return kwargs


def least_privilege_grants(database: str, user: str, prefix: str) -> list[str]:
    """The narrowest grants the API needs: read osTicket, run its own two queue tables."""
    return [
        f"GRANT SELECT ON `{database}`.* TO '{user}'@'%';",
        f"GRANT INSERT, DELETE ON `{database}`.`{prefix}integration_inbox` TO '{user}'@'%';",
        f"GRANT UPDATE, DELETE ON `{database}`.`{prefix}integration_outbox` TO '{user}'@'%';",
    ]


def grants_are_broad(grants: list[str]) -> bool:
    """True if any grant gives write access beyond the two queue tables."""
    for grant in grants:
        g = grant.upper()
        if not g.startswith("GRANT "):
            continue
        privileges = g[len("GRANT "): g.index(" ON ")]
        if not re.search(r"ALL PRIVILEGES|INSERT|UPDATE|DELETE|DROP|ALTER", privileges):
            continue
        target = g[g.index(" ON ") + 4: g.index(" TO ")]
        if "INTEGRATION_INBOX" in target or "INTEGRATION_OUTBOX" in target:
            continue
        return True
    return False


# ── checks ────────────────────────────────────────────────────────────────────


async def check_database(session: AsyncSession) -> Check:
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:
        return Check("database connection", FAIL, str(exc)[:200])
    return Check("database connection", OK)


async def check_osticket_schema(session: AsyncSession) -> Check:
    name = "osTicket tables"
    try:
        row = (
            await session.execute(
                repo._sql("SELECT value FROM ost_config WHERE namespace = 'core' AND `key` = 'schema_signature'")
            )
        ).first()
    except Exception as exc:
        return Check(
            name, FAIL,
            f"no {settings.OSTICKET_TABLE_PREFIX}config table — wrong OSTICKET_TABLE_PREFIX or database? ({str(exc).splitlines()[0][:120]})",
        )
    if not row:
        return Check(name, WARN, "osTicket is not installed in this database (no schema signature)")
    return Check(name, OK, f"prefix {settings.OSTICKET_TABLE_PREFIX!r}")


async def check_integration_tables(session: AsyncSession) -> list[Check]:
    checks = []
    for model in (IntegrationInbox, IntegrationOutbox):
        table = model.__tablename__
        try:
            rows = (
                await session.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = DATABASE() AND table_name = :t"
                    ),
                    {"t": table},
                )
            ).all()
        except Exception as exc:
            checks.append(Check(f"table {table}", FAIL, str(exc)[:200]))
            continue
        have = {r[0].lower() for r in rows}
        if not have:
            checks.append(Check(
                f"table {table}", FAIL,
                "missing — enable the fast-ticket plugin in osTicket (Admin Panel → Manage → Plugins) "
                "or run bridge/install.php",
            ))
            continue
        missing = {c.name for c in model.__table__.columns} - have
        checks.append(
            Check(f"table {table}", FAIL, f"missing columns {sorted(missing)} — upgrade the plugin")
            if missing else Check(f"table {table}", OK)
        )
    return checks


async def check_read_queries(session: AsyncSession) -> list[Check]:
    """Run every read function against the live schema; report each one that errors."""
    failures, ran, skipped = [], 0, []
    for name, func in inspect.getmembers(repo, inspect.iscoroutinefunction):
        if name.startswith("_") or func.__module__ != repo.__name__:
            continue
        kwargs = read_call_kwargs(func)
        if kwargs is None:
            skipped.append(name)
            continue
        variants = [kwargs] + ([{**kwargs, "manage": False}] if "manage" in kwargs else [])
        for variant in variants:
            try:
                await func(session, **variant)
                ran += 1
            except Exception as exc:
                failures.append(f"{name}: {str(exc).splitlines()[0][:160]}")
                await session.rollback()
    checks = [
        Check(
            "read queries", FAIL if failures else OK,
            f"{ran} ran" + (f", {len(failures)} failed:\n      " + "\n      ".join(failures) if failures else ""),
        )
    ]
    if skipped:
        checks.append(Check("read queries (not exercised)", WARN, ", ".join(skipped)))
    return checks


async def check_handshake(fetch: Callable[[str, str], Awaitable[dict]] | None = None) -> list[Check]:
    url = handshake_url(settings.OSTICKET_AUTH_URL)
    try:
        info = await (fetch or _fetch_handshake)(url, settings.FASTAPI_INTEGRATION_KEY)
    except Exception as exc:
        return [Check("bridge plugin handshake", FAIL, f"{url}: {str(exc)[:160]}")]
    checks = [Check("bridge plugin handshake", OK, f"plugin {info.get('plugin_version')}, PHP {info.get('php_version')}")]
    version = info.get("osticket_version")
    checks.append(
        Check("osTicket version", OK, version)
        if is_tested_version(version)
        else Check("osTicket version", WARN,
                   f"{version} — tested against {', '.join(TESTED_OSTICKET_VERSIONS)}; run the e2e suite before relying on it")
    )
    if info.get("table_prefix") != settings.OSTICKET_TABLE_PREFIX:
        checks.append(Check(
            "table prefix agreement", FAIL,
            f"osTicket uses {info.get('table_prefix')!r} but OSTICKET_TABLE_PREFIX is {settings.OSTICKET_TABLE_PREFIX!r}",
        ))
    if not info.get("tables_ready"):
        checks.append(Check("plugin tables", FAIL, "the plugin reports its tables are missing"))
    if not info.get("read_available"):
        checks.append(Check(
            "bridge read endpoint", WARN,
            "the installed plugin has no read endpoint: canned responses cannot be filled in and files kept by a "
            "storage plugin cannot be downloaded — update the plugin (make bridge-zip)",
        ))
    if not info.get("worker_available"):
        checks.append(Check(
            "inbox worker", WARN,
            "PHP has no pcntl extension: worker.php cannot re-exec between commands — commands run at "
            "cron speed (about once a minute); install pcntl or accept that",
        ))
    return checks


async def _fetch_handshake(url: str, key: str) -> dict:
    async with httpx.AsyncClient(timeout=settings.OSTICKET_AUTH_TIMEOUT) as client:
        response = await client.get(url, headers={"X-Integration-Key": key})
    if response.status_code == 403:
        raise RuntimeError("403 — FASTAPI_INTEGRATION_KEY does not match the plugin's key")
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code} (is the plugin enabled? is OSTICKET_AUTH_URL right?)")
    return response.json()


async def check_privileges(session: AsyncSession) -> Check:
    name = "database user privileges"
    try:
        grants = [row[0] for row in (await session.execute(text("SHOW GRANTS FOR CURRENT_USER()"))).all()]
        user, database = (await session.execute(text("SELECT SUBSTRING_INDEX(CURRENT_USER(), '@', 1), DATABASE()"))).one()
    except Exception as exc:
        return Check(name, WARN, f"could not read grants: {str(exc)[:120]}")
    if not grants_are_broad(grants):
        return Check(name, OK, "read-only on osTicket tables")
    advice = "\n      ".join(least_privilege_grants(database, user, settings.OSTICKET_TABLE_PREFIX))
    return Check(
        name, WARN,
        "this user can write to osTicket's own tables; the API only needs:\n      " + advice,
    )


def connection_pressure(max_used: int, limit: int) -> str:
    """``ok``/``warn``/``fail`` for how close the server has come to refusing connections."""
    if limit and max_used >= limit:
        return FAIL
    if limit and max_used >= limit * 0.8:
        return WARN
    return OK


async def check_connections(session: AsyncSession) -> Check:
    """A leak (a worker that never closes its connection) shows up as the high-water mark nearing max_connections,
    after which the next web request or login is refused with a 500."""
    name = "database connections"
    try:
        status = dict((await session.execute(text("SHOW GLOBAL STATUS LIKE 'Max_used_connections'"))).all())
        limit = int((await session.execute(text("SELECT @@max_connections"))).scalar_one())
        current = int((await session.execute(text("SELECT COUNT(*) FROM information_schema.processlist"))).scalar_one())
    except Exception as exc:
        return Check(name, WARN, f"could not read connection counters: {str(exc).splitlines()[0][:120]}")
    peak = int(status.get("Max_used_connections", 0))
    verdict = connection_pressure(peak, limit)
    detail = f"{current} open now, peak {peak} of {limit} allowed since the server started"
    if verdict != OK:
        detail += " — something may be leaking connections (check the worker), or raise max_connections"
    return Check(name, verdict, detail)


async def check_timezone(session: AsyncSession) -> Check:
    """Which timezone the API reads osTicket's naive timestamps in (it answers every timestamp as UTC with a Z)."""
    from app.core import timezone as tzmod
    name = "timestamp timezone"
    try:
        source = await tzmod.load_database_timezone(session, settings.DB_TIMEZONE)
    except Exception as exc:
        return Check(name, WARN, f"could not read it: {str(exc).splitlines()[0][:120]}")
    if source.startswith("UTC (could not"):
        return Check(name, WARN, source)
    return Check(name, OK, source)


async def check_queue(session: AsyncSession) -> list[Check]:
    checks = []
    try:
        stuck = (await session.execute(
            text(f"SELECT COUNT(*) FROM {IntegrationInbox.__tablename__} "
                 "WHERE status = 'pending' AND created_at < DATE_SUB(NOW(), INTERVAL 2 MINUTE)")
        )).scalar_one()
    except Exception:
        return checks   # the table check has already reported this
    checks.append(
        Check("command queue", WARN, f"{stuck} command(s) pending for over 2 minutes — is a worker or cron running?")
        if stuck else Check("command queue", OK, "nothing stuck")
    )
    return checks


async def run_checks(session: AsyncSession, fetch=None) -> list[Check]:
    checks = [await check_database(session)]
    if checks[0].status == FAIL:
        return checks
    checks.append(await check_osticket_schema(session))
    checks += await check_integration_tables(session)
    checks += await check_read_queries(session)
    checks += await check_handshake(fetch)
    checks.append(await check_privileges(session))
    checks.append(await check_connections(session))
    checks.append(await check_timezone(session))
    checks += await check_queue(session)
    return checks
