"""Read-only SQL queries against osTicket's native tables.

Rules:
  - SELECT only. No INSERT/UPDATE/DELETE against ost_* tables.
  - All queries are parameterised — no string interpolation.
  - Return raw RowMapping objects; callers decide how to shape the response.
  - One function per logical resource. Group by domain with section comments.
"""

import json
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _nullable_int(value) -> int | None:
    """Return None for falsy DB ints (osTicket uses 0 as 'not set')."""
    return int(value) if value else None


# ── Tickets ───────────────────────────────────────────────────────────────────


_TICKET_SELECT = """
    SELECT
        t.ticket_id,
        t.number,
        COALESCE(te_first.title, '') AS subject,
        ts.id                        AS status_id,
        ts.name                      AS status_name,
        ts.state                     AS status_state,
        d.id                         AS dept_id,
        d.name                       AS dept_name,
        IF(t.staff_id > 0, t.staff_id, NULL)                         AS assignee_staff_id,
        IF(t.staff_id > 0, CONCAT(s.firstname, ' ', s.lastname), NULL) AS assignee_name,
        IF(t.team_id > 0, t.team_id, NULL)  AS team_id,
        IF(t.team_id > 0, tm.name, NULL)    AS team_name,
        u.id                         AS user_id,
        u.name                       AS user_name,
        ht.topic_id,
        ht.topic                     AS topic_name,
        sp.id                        AS sla_id,
        sp.name                      AS sla_name,
        t.source,
        t.isanswered                 AS is_answered,
        t.created,
        t.updated,
        t.closed,
        t.duedate                    AS due_date
    FROM ost_ticket t
    LEFT JOIN ost_ticket_status ts ON t.status_id = ts.id
    LEFT JOIN (
        SELECT thread_id, MIN(id) AS first_id
        FROM ost_thread_entry
        WHERE type = 'M'
        GROUP BY thread_id
    ) te_min ON te_min.thread_id = t.thread_id
    LEFT JOIN ost_thread_entry te_first ON te_first.id = te_min.first_id
    LEFT JOIN ost_department d   ON t.dept_id  = d.id
    LEFT JOIN ost_staff s        ON t.staff_id = s.staff_id AND t.staff_id > 0
    LEFT JOIN ost_team tm        ON t.team_id  = tm.team_id AND t.team_id > 0
    LEFT JOIN ost_user u         ON t.user_id  = u.id
    LEFT JOIN ost_help_topic ht  ON t.topic_id = ht.topic_id
    LEFT JOIN ost_sla sp         ON t.sla_id   = sp.id
"""


async def list_tickets(
    session: AsyncSession,
    *,
    status_id: int | None = None,
    dept_id: int | None = None,
    assignee_staff_id: int | None = None,
    assignee_team_id: int | None = None,
    topic_id: int | None = None,
    sla_id: int | None = None,
    is_answered: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list:
    """Return a filtered, paginated list of ticket rows."""
    where_clauses = ["1=1"]
    params: dict = {"limit": limit, "offset": offset}

    if status_id is not None:
        where_clauses.append("t.status_id = :status_id")
        params["status_id"] = status_id
    if dept_id is not None:
        where_clauses.append("t.dept_id = :dept_id")
        params["dept_id"] = dept_id
    if assignee_staff_id is not None:
        where_clauses.append("t.staff_id = :assignee_staff_id")
        params["assignee_staff_id"] = assignee_staff_id
    if assignee_team_id is not None:
        where_clauses.append("t.team_id = :assignee_team_id")
        params["assignee_team_id"] = assignee_team_id
    if topic_id is not None:
        where_clauses.append("t.topic_id = :topic_id")
        params["topic_id"] = topic_id
    if sla_id is not None:
        where_clauses.append("t.sla_id = :sla_id")
        params["sla_id"] = sla_id
    if is_answered is not None:
        where_clauses.append("t.isanswered = :is_answered")
        params["is_answered"] = 1 if is_answered else 0

    sql = (
        _TICKET_SELECT
        + " WHERE " + " AND ".join(where_clauses)
        + " ORDER BY t.updated DESC"
        + " LIMIT :limit OFFSET :offset"
    )
    result = await session.execute(text(sql), params)
    return list(result.mappings().all())


async def get_ticket(session: AsyncSession, ticket_id: int) -> dict | None:
    """Return a single ticket row dict, or None if not found."""
    sql = _TICKET_SELECT + " WHERE t.ticket_id = :ticket_id"
    result = await session.execute(text(sql), {"ticket_id": ticket_id})
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def get_ticket_thread(session: AsyncSession, ticket_id: int) -> list:
    """Return thread entries for a ticket ordered by creation time."""
    sql = """
        SELECT
            te.id,
            te.type,
            te.title,
            te.body,
            te.staff_id,
            te.user_id,
            te.ip_address,
            te.created,
            CASE
                WHEN te.staff_id > 0 THEN CONCAT(s.firstname, ' ', s.lastname)
                WHEN te.user_id  > 0 THEN u.name
                ELSE NULL
            END AS poster
        FROM ost_ticket t
        JOIN ost_thread_entry te ON te.thread_id = t.thread_id
        LEFT JOIN ost_staff s ON te.staff_id = s.staff_id AND te.staff_id > 0
        LEFT JOIN ost_user  u ON te.user_id  = u.id       AND te.user_id  > 0
        WHERE t.ticket_id = :ticket_id
        ORDER BY te.created ASC, te.id ASC
    """
    result = await session.execute(text(sql), {"ticket_id": ticket_id})
    return list(result.mappings().all())


async def get_ticket_collaborators(session: AsyncSession, ticket_id: int) -> list:
    """Return collaborators for a ticket."""
    sql = """
        SELECT
            c.id,
            c.user_id,
            u.name,
            ue.address AS email
        FROM ost_collaborator c
        JOIN ost_ticket t ON t.thread_id = c.thread_id
        JOIN ost_user u ON c.user_id = u.id
        LEFT JOIN ost_user_email ue ON ue.user_id = u.id
        WHERE t.ticket_id = :ticket_id
        ORDER BY c.id ASC
    """
    result = await session.execute(text(sql), {"ticket_id": ticket_id})
    return list(result.mappings().all())


# ── Staff ─────────────────────────────────────────────────────────────────────


async def get_staff_permissions(
    session: AsyncSession, staff_id: int
) -> tuple[list[str], bool]:
    """Return (permissions_list, is_admin) for a staff member.

    Called at login time to embed permissions into the JWT.
    """
    result = await session.execute(
        text("SELECT isadmin, role_id FROM ost_staff WHERE staff_id = :sid"),
        {"sid": staff_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        return [], False

    is_admin = bool(row["isadmin"])
    if is_admin:
        return [], True

    role_id = row["role_id"]
    if not role_id:
        return [], False

    role_result = await session.execute(
        text("SELECT permissions FROM ost_role WHERE id = :rid"),
        {"rid": role_id},
    )
    role_row = role_result.mappings().one_or_none()
    if role_row is None:
        return [], False

    perm_map: dict = json.loads(role_row["permissions"] or "{}")
    enabled = [k for k, v in perm_map.items() if v]
    return enabled, False


# ── Organizations ─────────────────────────────────────────────────────────────


async def list_organizations(session: AsyncSession, limit: int = 50, offset: int = 0) -> list:
    """Populate in Phase 4."""
    raise NotImplementedError("list_organizations — implement in Phase 4")


# ── Tasks (top-level) ─────────────────────────────────────────────────────────


async def list_tasks_from_osticket(
    session: AsyncSession, limit: int = 50, offset: int = 0
) -> list:
    """Populate in Phase 3."""
    raise NotImplementedError("list_tasks_from_osticket — implement in Phase 3")
