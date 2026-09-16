"""Read-only SQL queries against osTicket's native tables.

Rules:
  - SELECT only. No INSERT/UPDATE/DELETE against ost_* tables.
  - All queries are parameterised — no string interpolation.
  - Return raw RowMapping objects; callers decide how to shape the response.
  - One function per logical resource. Group by domain with section comments.

Queries are populated as each API domain is implemented (Phase 2 onward).
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


# ── Tickets ───────────────────────────────────────────────────────────────────


async def list_tickets(
    session: AsyncSession,
    status_id: int | None = None,
    dept_id: int | None = None,
    assignee_staff_id: int | None = None,
    assignee_team_id: int | None = None,
    topic_id: int | None = None,
    sla_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list:
    """Return a flat list of ticket rows with enriched fields.

    Populated in Phase 2.
    """
    # TODO: implement in Phase 2
    raise NotImplementedError("list_tickets — implement in Phase 2")


async def get_ticket(session: AsyncSession, ticket_id: int) -> dict | None:
    """Return a single ticket dict or None if not found.

    Populated in Phase 2.
    """
    raise NotImplementedError("get_ticket — implement in Phase 2")


async def get_ticket_thread(session: AsyncSession, ticket_id: int) -> list:
    """Return thread entries for a ticket ordered by creation time.

    Populated in Phase 2.
    """
    raise NotImplementedError("get_ticket_thread — implement in Phase 2")


# ── Staff ─────────────────────────────────────────────────────────────────────


async def list_staff(session: AsyncSession, active_only: bool = True) -> list:
    """Return staff rows.  Populated in Phase 5."""
    raise NotImplementedError("list_staff — implement in Phase 5")


async def get_staff_permissions(
    session: AsyncSession, staff_id: int
) -> tuple[list[str], bool]:
    """Return (permissions_list, is_admin) for a staff member.

    Called at login time to embed permissions into the JWT.
    """
    # is_admin flag lives in ost_staff.isadmin
    result = await session.execute(
        text("SELECT isadmin, role_id FROM ost_staff WHERE staff_id = :sid"),
        {"sid": staff_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        return [], False

    is_admin = bool(row["isadmin"])
    if is_admin:
        return [], True  # admins bypass all permission checks; list is irrelevant

    role_id = row["role_id"]
    if not role_id:
        return [], False

    # ost_role.permissions is a JSON object: {"ticket.create": 1, ...}
    role_result = await session.execute(
        text("SELECT permissions FROM ost_role WHERE id = :rid"),
        {"rid": role_id},
    )
    role_row = role_result.mappings().one_or_none()
    if role_row is None:
        return [], False

    import json
    perm_map: dict = json.loads(role_row["permissions"] or "{}")
    # Keep only keys where the value is truthy (osTicket stores 1 for enabled)
    enabled = [k for k, v in perm_map.items() if v]
    return enabled, False


# ── Organizations ─────────────────────────────────────────────────────────────


async def list_organizations(session: AsyncSession) -> list:
    """Populate in Phase 4."""
    raise NotImplementedError("list_organizations — implement in Phase 4")


# ── Tasks ─────────────────────────────────────────────────────────────────────


async def list_tasks_from_osticket(session: AsyncSession) -> list:
    """Populate in Phase 3."""
    raise NotImplementedError("list_tasks_from_osticket — implement in Phase 3")
