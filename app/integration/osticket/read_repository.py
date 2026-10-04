"""Read-only SQL queries against osTicket's native tables.

Rules:
  - SELECT only. No INSERT/UPDATE/DELETE against ost_* tables.
  - All queries are parameterised — no string interpolation.
  - Return raw RowMapping objects; callers decide how to shape the response.
  - One function per logical resource. Group by domain with section comments.
"""

import json
import logging
import re
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.names import sort_columns

if TYPE_CHECKING:   # the type only; importing it at runtime would be circular
    from app.core.access import AccessContext
    from app.core.scope import _Unrestricted

    Scope = AccessContext | _Unrestricted

logger = logging.getLogger(__name__)

_TABLE_TOKEN = re.compile(r"(?<![A-Za-z0-9_])ost_(?=[a-z])")


def prefixed(sql: str, prefix: str | None = None) -> str:
    """Rewrite the ``ost_`` table names in ``sql`` to the instance's real table prefix.

    The queries below are written against osTicket's default ``ost_`` prefix; an instance
    installed with another prefix has ``ost_ticket`` as e.g. ``support_ticket``. This is the one
    place that difference is handled — every query goes through ``_sql``.
    """
    prefix = settings.OSTICKET_TABLE_PREFIX if prefix is None else prefix
    return sql if prefix == "ost_" else _TABLE_TOKEN.sub(prefix, sql)


def _sql(sql: str):
    return text(prefixed(sql))


# ── Helpers ───────────────────────────────────────────────────────────────────


def _nullable_int(value) -> int | None:
    """Return None for falsy DB ints (osTicket uses 0 as 'not set')."""
    return int(value) if value else None


# ── Visibility: what an agent may see ─────────────────────────────────────────
#
# These reproduce osTicket's own rules, from its source, not from a description of them:
#
#   ticket lists   Staff::getTicketsVisibility()  (include/class.staff.php)
#   ticket detail  Ticket::checkStaffPerm()       (include/class.ticket.php) — scp/tickets.php gates the page on it
#   task lists     the "Impose visibility constraints" block of include/staff/tasks.inc.php
#   task detail    Task::checkStaffPerm()         (include/class.task.php)   — scp/tasks.php gates the page on it
#
# The list and detail rules genuinely differ in osTicket (a referral counts on the detail page in any
# state but in a list only while the ticket is open; a closed task can be opened by any agent but is not
# listed unless it is in their department), and both are reproduced as they are.
#
# ``access`` is required everywhere and has no default: pass the caller's AccessContext, or
# ``scope.UNRESTRICTED`` for the rare deliberate exception. Ids are inlined because they are integers read
# from osTicket into the AccessContext, never anything a request supplied.


def _ids(values) -> str:
    return ",".join(str(int(v)) for v in sorted(values))


def _in(column: str, ids) -> str:
    """``column IN (...)``, or a condition that is never true for an empty list (``IN ()`` is invalid SQL)."""
    return f"{column} IN ({_ids(ids)})" if ids else "1 = 0"


def _referral(kind: str, ids) -> str:
    """The ticket's thread (a merged child's thread has type 'C') is referred to agent 'S', team 'E' or dept 'D' in ``ids``."""
    return (
        "EXISTS (SELECT 1 FROM ost_thread rth JOIN ost_thread_referral rr ON rr.thread_id = rth.id "
        "WHERE rth.object_id = t.ticket_id AND rth.object_type IN ('T', 'C') "
        f"AND rr.object_type = '{kind}' AND rr.object_id IN ({_ids(ids)}))"
    )


def ticket_visibility(access: "Scope", *, detail: bool) -> str:
    """SQL condition over ``ost_ticket t`` joined to ``ost_ticket_status ts``."""
    if getattr(access, "unrestricted", False):
        return "1 = 1"
    me, teams, depts = int(access.staff_id), access.team_ids, access.dept_ids
    in_dept = (not access.assigned_only) and bool(depts)

    if detail:   # Ticket::checkStaffPerm(): department access, or open and assigned to me/my team, or referred (any state)
        assigned = [f"t.staff_id = {me}"] + ([f"t.team_id IN ({_ids(teams)})"] if teams else [])
        parts = [f"t.dept_id IN ({_ids(depts)})"] if in_dept else []
        parts.append(f"(ts.state = 'open' AND ({' OR '.join(assigned)}))")
        parts.append(_referral("S", [me]))
        if depts:
            parts.append(_referral("D", depts))
        if teams:
            parts.append(_referral("E", teams))
        return "(" + " OR ".join(parts) + ")"

    # Staff::getTicketsVisibility(): open and mine (or my team's, or referred to either); else my departments
    assigned = [f"t.staff_id = {me}", _referral("S", [me])]
    if teams:
        assigned += [f"t.team_id IN ({_ids(teams)})", _referral("E", teams)]
    parts = [f"(ts.state = 'open' AND ({' OR '.join(assigned)}))"]
    if in_dept:
        parts += [f"t.dept_id IN ({_ids(depts)})", _referral("D", depts)]
    return "(" + " OR ".join(parts) + ")"


def task_visibility(access: "Scope", *, detail: bool) -> str:
    """SQL condition over ``ost_task t`` (open = flags bit 0x1)."""
    if getattr(access, "unrestricted", False):
        return "1 = 1"
    me, teams, depts = int(access.staff_id), access.team_ids, access.dept_ids
    in_dept = (not access.assigned_only) and bool(depts)

    if detail:   # Task::checkStaffPerm(): denied only if outside my departments AND open AND not mine AND not my team's
        parts = [f"t.dept_id IN ({_ids(depts)})"] if in_dept else []
        parts += ["(t.flags & 1) = 0", f"t.staff_id = {me}"]
        if teams:
            parts.append(f"t.team_id IN ({_ids(teams)})")
        return "(" + " OR ".join(parts) + ")"

    parts = [
        f"((t.flags & 1) = 1 AND t.staff_id = {me})",
        # a task of a ticket that is assigned to me and open
        "(t.object_type = 'T' AND EXISTS (SELECT 1 FROM ost_ticket pt JOIN ost_ticket_status pts ON pts.id = pt.status_id "
        f"WHERE pt.ticket_id = t.object_id AND pt.staff_id = {me} AND pts.state = 'open'))",
    ]
    if in_dept:
        parts.append(f"t.dept_id IN ({_ids(depts)})")
    if teams:
        parts.append(f"((t.flags & 1) = 1 AND t.team_id IN ({_ids(teams)}))")
    return "(" + " OR ".join(parts) + ")"


# ── Tickets ───────────────────────────────────────────────────────────────────


_TICKET_SELECT = """
    SELECT
        t.ticket_id,
        t.number,
        COALESCE(c.subject, '')      AS subject,
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
        pr.priority_id               AS priority_id,
        pr.priority_desc             AS priority_name,
        t.source,
        t.isanswered                 AS is_answered,
        t.created,
        t.updated,
        t.closed,
        t.duedate                    AS due_date
    FROM ost_ticket t
    LEFT JOIN ost_ticket_status ts ON t.status_id = ts.id
    -- the subject is an answer of the ticket form, mirrored in ost_ticket__cdata
    LEFT JOIN ost_ticket__cdata c ON c.ticket_id = t.ticket_id
    LEFT JOIN ost_department d   ON t.dept_id  = d.id
    LEFT JOIN ost_staff s        ON t.staff_id = s.staff_id AND t.staff_id > 0
    LEFT JOIN ost_team tm        ON t.team_id  = tm.team_id AND t.team_id > 0
    LEFT JOIN ost_user u         ON t.user_id  = u.id
    LEFT JOIN ost_help_topic ht  ON t.topic_id = ht.topic_id
    LEFT JOIN ost_sla sp         ON t.sla_id   = sp.id
    -- the priority is an answer of the ticket form too (cdata holds its id)
    LEFT JOIN ost_ticket_priority pr ON pr.priority_id = c.priority
"""


async def list_tickets(
    session: AsyncSession,
    *,
    access: "Scope",
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
    where_clauses = [ticket_visibility(access, detail=False)]
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
    result = await session.execute(_sql(sql), params)
    return list(result.mappings().all())


async def get_ticket(session: AsyncSession, ticket_id: int, *, access: "Scope") -> dict | None:
    """Return a single ticket row dict, or None if it does not exist *or the agent may not see it*."""
    sql = _TICKET_SELECT + " WHERE t.ticket_id = :ticket_id AND " + ticket_visibility(access, detail=True)
    result = await session.execute(_sql(sql), {"ticket_id": ticket_id})
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def ticket_is_visible(session: AsyncSession, ticket_id: int, *, access: "Scope") -> bool:
    """Is this ticket in the agent's ticket list? (Used to decide who is told about an event.)"""
    sql = (
        "SELECT 1 FROM ost_ticket t JOIN ost_ticket_status ts ON ts.id = t.status_id "
        "WHERE t.ticket_id = :ticket_id AND " + ticket_visibility(access, detail=False) + " LIMIT 1"
    )
    return (await session.execute(_sql(sql), {"ticket_id": ticket_id})).first() is not None


async def task_is_visible(session: AsyncSession, task_id: int, *, access: "Scope") -> bool:
    """Is this task in the agent's task list? (Used to decide who is told about an event.)"""
    sql = "SELECT 1 FROM ost_task t WHERE t.id = :task_id AND " + task_visibility(access, detail=False) + " LIMIT 1"
    return (await session.execute(_sql(sql), {"task_id": task_id})).first() is not None


def _visible_ticket(access: "Scope") -> str:
    """``AND EXISTS`` clause: the ticket bound as :ticket_id is one the agent may open."""
    return (
        " AND EXISTS (SELECT 1 FROM ost_ticket t JOIN ost_ticket_status ts ON ts.id = t.status_id "
        "WHERE t.ticket_id = :ticket_id AND " + ticket_visibility(access, detail=True) + ")"
    )


def _visible_task(access: "Scope") -> str:
    """``AND EXISTS`` clause: the task bound as :task_id is one the agent may open."""
    return (
        " AND EXISTS (SELECT 1 FROM ost_task t WHERE t.id = :task_id AND "
        + task_visibility(access, detail=True) + ")"
    )


async def get_ticket_thread(session: AsyncSession, ticket_id: int, *, access: "Scope") -> list:
    """Return thread entries for a ticket ordered by creation time (nothing if the agent may not open it)."""
    sql = """
        SELECT
            te.id,
            te.type,
            te.title,
            COALESCE(te.body, '') AS body,
            te.staff_id,
            te.user_id,
            te.ip_address,
            te.created,
            (te.flags & 2) > 0 AS is_edited,
            CASE
                WHEN te.staff_id > 0 THEN CONCAT(s.firstname, ' ', s.lastname)
                WHEN te.user_id  > 0 THEN u.name
                ELSE NULL
            END AS poster
        FROM ost_thread th
        JOIN ost_thread_entry te ON te.thread_id = th.id
        LEFT JOIN ost_staff s ON te.staff_id = s.staff_id AND te.staff_id > 0
        LEFT JOIN ost_user  u ON te.user_id  = u.id       AND te.user_id  > 0
        WHERE th.object_type = 'T' AND th.object_id = :ticket_id
          AND (te.flags & 4) = 0   -- FLAG_HIDDEN: the superseded version of an edited entry
        {visible}
        ORDER BY te.created ASC, te.id ASC
    """.replace("{visible}", _visible_ticket(access))
    result = await session.execute(_sql(sql), {"ticket_id": ticket_id})
    return list(result.mappings().all())


_CUSTOM_FIELDS_SQL = """
    SELECT ff.name, ff.label, ff.type, ev.value, ev.value_id
    FROM ost_form_entry fe
    JOIN ost_form_entry_values ev ON ev.entry_id = fe.id
    JOIN ost_form_field ff ON ff.id = ev.field_id
    WHERE fe.object_type = :object_type AND fe.object_id = :object_id
      AND (ff.flags & 1) > 0        -- enabled
      AND (ff.flags & 4096) > 0     -- visible to agents (FLAG_AGENT_VIEW)
      AND ff.name <> ''
      {visible}
    ORDER BY fe.sort, ff.sort, ff.id
"""


async def get_ticket_custom_fields(session: AsyncSession, ticket_id: int, *, access: "Scope") -> list:
    """Answers to the ticket's form fields that agents can see (nothing if the agent may not open the ticket)."""
    sql = _CUSTOM_FIELDS_SQL.replace("{visible}", _visible_ticket(access))
    result = await session.execute(_sql(sql), {"object_type": "T", "object_id": ticket_id, "ticket_id": ticket_id})
    return list(result.mappings().all())


async def get_ticket_collaborators(session: AsyncSession, ticket_id: int, *, access: "Scope") -> list:
    """Return collaborators for a ticket (nothing if the agent may not open it)."""
    sql = """
        SELECT
            c.id,
            c.user_id,
            u.name,
            COALESCE(ue.address, '') AS email,
            (c.flags & 1) > 0 AS is_active,
            (c.flags & 2) > 0 AS is_cc
        FROM ost_thread_collaborator c
        JOIN ost_thread th ON th.id = c.thread_id AND th.object_type = 'T'
        JOIN ost_user u ON c.user_id = u.id
        LEFT JOIN ost_user_email ue ON ue.id = u.default_email_id
        WHERE th.object_id = :ticket_id
        {visible}
        ORDER BY c.id ASC
    """.replace("{visible}", _visible_ticket(access))
    result = await session.execute(_sql(sql), {"ticket_id": ticket_id})
    return list(result.mappings().all())


# ── Staff ─────────────────────────────────────────────────────────────────────


async def get_staff_row(session: AsyncSession, staff_id: int) -> dict | None:
    """The staff row that decides who an agent is (flags, primary department and role, global permissions),
    or None if there is no such staff member. Read on every request: it carries the enabled/admin flags."""
    staff = (
        await session.execute(
            _sql(
                "SELECT staff_id, username, firstname, lastname, email, isactive, isadmin, dept_id, "
                "role_id, assigned_only, permissions FROM ost_staff WHERE staff_id = :sid"
            ),
            {"sid": staff_id},
        )
    ).mappings().first()
    return dict(staff) if staff is not None else None


async def get_staff_grants(session: AsyncSession, staff_id: int, role_id: int = 0) -> dict:
    """What else decides an agent's access, given their staff row's primary ``role_id``.

    Mirrors the queries behind osTicket's Staff::getRoles(), getDepartments() and getTeams()
    (include/class.staff.php): the extra-department access rows, the permissions of every role involved,
    team membership, and the departments the agent can see — primary, extra and managed departments plus
    their sub-departments (a department's ``path`` is ``/<ancestor ids>/<own id>/``).
    """
    params = {"sid": staff_id}
    dept_access = (
        await session.execute(
            _sql("SELECT dept_id, role_id FROM ost_staff_dept_access WHERE staff_id = :sid"), params
        )
    ).mappings().all()

    role_ids = {int(role_id or 0)} | {int(r["role_id"] or 0) for r in dept_access}
    roles: dict[int, str | None] = {}
    if role_ids - {0}:
        role_rows = (
            await session.execute(
                _sql("SELECT id, permissions FROM ost_role WHERE id IN (" + ",".join(str(int(i)) for i in role_ids if i) + ")")
            )
        ).all()
        roles = {int(r[0]): r[1] for r in role_rows}

    team_ids = [
        int(r[0])
        for r in (
            await session.execute(_sql("SELECT team_id FROM ost_team_member WHERE staff_id = :sid"), params)
        ).all()
    ]

    dept_ids = [
        int(r[0])
        for r in (
            await session.execute(
                _sql(
                    "SELECT DISTINCT d.id FROM ost_staff s "
                    "LEFT JOIN ost_staff_dept_access g ON s.staff_id = g.staff_id "
                    "INNER JOIN ost_department d ON (LOCATE(CONCAT('/', s.dept_id, '/'), d.path) "
                    "OR d.manager_id = s.staff_id OR LOCATE(CONCAT('/', g.dept_id, '/'), d.path)) "
                    "WHERE s.staff_id = :sid"
                ),
                params,
            )
        ).all()
    ]
    return {
        "dept_access": [dict(r) for r in dept_access],
        "roles": roles,
        "team_ids": team_ids,
        "dept_ids": dept_ids,
    }


async def list_staff(session: AsyncSession, limit: int = 50, offset: int = 0) -> list:
    sql = """
        SELECT
            s.staff_id AS id,
            s.username,
            s.firstname,
            s.lastname,
            s.email,
            s.isactive AS is_active,
            s.isadmin AS is_admin,
            s.dept_id,
            s.role_id,
            s.created,
            s.updated
        FROM ost_staff s
        ORDER BY s.username ASC LIMIT :limit OFFSET :offset
    """
    result = await session.execute(_sql(sql), {"limit": limit, "offset": offset})
    return list(result.mappings().all())


async def get_staff(session: AsyncSession, staff_id: int) -> dict | None:
    sql = """
        SELECT
            s.staff_id AS id,
            s.username,
            s.firstname,
            s.lastname,
            s.email,
            s.isactive AS is_active,
            s.isadmin AS is_admin,
            s.dept_id,
            s.role_id,
            s.created,
            s.updated,
            COALESCE(s.phone, '')     AS phone,
            COALESCE(s.mobile, '')    AS mobile,
            COALESCE(s.phone_ext, '') AS phone_ext,
            COALESCE(s.notes, '')     AS notes,
            s.isvisible               AS is_visible,
            s.onvacation              AS on_vacation,
            s.assigned_only,
            s.permissions,
            s.extra
        FROM ost_staff s
        WHERE s.staff_id = :staff_id
    """
    result = await session.execute(_sql(sql), {"staff_id": staff_id})
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def get_staff_dept_access(session: AsyncSession, staff_id: int) -> list[dict]:
    """The agent's extra-department access: department, role in it, and whether it sends alerts (flags bit 1)."""
    sql = """
        SELECT dept_id, role_id, (flags & 1) > 0 AS alerts
        FROM ost_staff_dept_access
        WHERE staff_id = :staff_id
        ORDER BY dept_id
    """
    result = await session.execute(_sql(sql), {"staff_id": staff_id})
    return [dict(r) for r in result.mappings().all()]


async def get_staff_teams(session: AsyncSession, staff_id: int) -> list[int]:
    sql = "SELECT team_id FROM ost_team_member WHERE staff_id = :staff_id"
    result = await session.execute(_sql(sql), {"staff_id": staff_id})
    return [r["team_id"] for r in result.mappings().all()]


# ── Departments ───────────────────────────────────────────────────────────────

async def list_departments(
    session: AsyncSession, *, access: "Scope", limit: int = 50, offset: int = 0
) -> list:
    """Departments. ``access`` narrows the list to the departments the agent may see (``Staff::getDepartmentNames``)
    unless it is ``UNRESTRICTED`` (the Admin Panel's full list)."""
    visible = "1 = 1" if getattr(access, "unrestricted", False) else _in("d.id", access.dept_ids)
    sql = """
        SELECT
            d.id,
            d.name,
            d.ispublic AS is_public,
            d.created,
            d.updated
        FROM ost_department d
        WHERE {visible}
        ORDER BY d.name ASC LIMIT :limit OFFSET :offset
    """.replace("{visible}", visible)
    result = await session.execute(_sql(sql), {"limit": limit, "offset": offset})
    return list(result.mappings().all())


async def get_department(session: AsyncSession, dept_id: int) -> dict | None:
    sql = """
        SELECT
            d.id,
            d.name,
            d.ispublic AS is_public,
            d.created,
            d.updated,
            d.manager_id,
            d.email_id,
            d.pid                       AS parent_id,
            d.sla_id,
            d.schedule_id,
            d.tpl_id                    AS template_id,
            d.autoresp_email_id         AS autoresponse_email_id,
            COALESCE(d.signature, '')   AS signature,
            d.group_membership,
            d.ticket_auto_response,
            d.message_auto_response,
            d.flags
        FROM ost_department d
        WHERE d.id = :dept_id
    """
    result = await session.execute(_sql(sql), {"dept_id": dept_id})
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def get_department_members(session: AsyncSession, dept_id: int) -> list[dict]:
    """Extended-access agents of a department (rows of ost_staff_dept_access): agent, role, alerts (flags bit 1)."""
    sql = """
        SELECT staff_id, role_id, (flags & 1) > 0 AS alerts
        FROM ost_staff_dept_access
        WHERE dept_id = :dept_id
        ORDER BY staff_id
    """
    result = await session.execute(_sql(sql), {"dept_id": dept_id})
    return [dict(r) for r in result.mappings().all()]


# ── Teams ─────────────────────────────────────────────────────────────────────

async def list_teams(session: AsyncSession, limit: int = 50, offset: int = 0) -> list:
    sql = """
        SELECT
            t.team_id AS id,
            t.name,
            (t.flags & 1) > 0 AS is_enabled,   -- Team::FLAG_ENABLED
            t.created,
            t.updated
        FROM ost_team t
        ORDER BY t.name ASC LIMIT :limit OFFSET :offset
    """
    result = await session.execute(_sql(sql), {"limit": limit, "offset": offset})
    return list(result.mappings().all())


async def get_team(session: AsyncSession, team_id: int) -> dict | None:
    sql = """
        SELECT
            t.team_id AS id,
            t.name,
            (t.flags & 1) > 0 AS is_enabled,   -- Team::FLAG_ENABLED
            t.created,
            t.updated,
            IF(t.lead_id > 0, t.lead_id, NULL) AS lead_id,
            COALESCE(t.notes, '') AS notes,
            (t.flags & 2) > 0 AS no_alerts   -- Team::FLAG_NOALERTS
        FROM ost_team t
        WHERE t.team_id = :team_id
    """
    result = await session.execute(_sql(sql), {"team_id": team_id})
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def get_team_members(session: AsyncSession, team_id: int) -> list[dict]:
    """Members with their assignment-alert preference (TeamMember::FLAG_ALERTS, bit 1)."""
    sql = """
        SELECT staff_id, (COALESCE(flags, 0) & 1) > 0 AS alerts
        FROM ost_team_member WHERE team_id = :team_id ORDER BY staff_id
    """
    result = await session.execute(_sql(sql), {"team_id": team_id})
    return [dict(r) for r in result.mappings().all()]


# ── Agents: the directory and who can be assigned ─────────────────────────────

async def get_agent_name_format(session: AsyncSession) -> str:
    """The instance's ``agent_name_format`` setting (osTicket's default is ``full``)."""
    result = await session.execute(_sql("SELECT value FROM ost_config WHERE namespace = 'core' AND `key` = 'agent_name_format'"))
    row = result.first()
    return (row[0] if row and row[0] else "full")


def _agent_visibility(access: "Scope") -> str:
    """``Staff::applyDeptVisibility()`` over ``ost_staff s``: without the ``visibility.agents`` permission an agent
    who has departments sees only agents of those departments (primary or extra access). An agent with no
    departments at all is not restricted — osTicket's own test is ``&& ($depts = $this->getDepts())``."""
    if getattr(access, "unrestricted", False) or access.has_global("visibility.agents") or not access.dept_ids:
        return "1 = 1"
    depts = _ids(access.dept_ids)
    return (f"(s.dept_id IN ({depts}) OR EXISTS (SELECT 1 FROM ost_staff_dept_access da "
            f"WHERE da.staff_id = s.staff_id AND da.dept_id IN ({depts})))")


_DIRECTORY_SORTS = {   # scp/directory.php's $sortOptions
    "email": ["s.email"], "dept": ["d.name"], "phone": ["s.phone"], "mobile": ["s.mobile"],
    "ext": ["s.phone_ext"], "created": ["s.created"], "login": ["s.lastlogin"],
}


def _like(term: str) -> str:
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


async def list_directory_agents(
    session: AsyncSession, *, access: "Scope", q: str | None = None, dept_id: int | None = None,
    sort: str = "name", descending: bool = False, name_format: str = "full", limit: int = 50, offset: int = 0,
) -> tuple[list, int]:
    """scp/directory.php: ``Staff::getDeptAgents()`` (agents of the departments the caller may see — inactive ones
    included, as osTicket lists them), a search over name / email / phone, and a primary-department filter."""
    where, params = [_agent_visibility(access)], {"limit": limit, "offset": offset}
    if q:
        if q.isnumeric():   # PHP's is_numeric() also admits "1.5" and "-3"; digits only here
            where.append("(s.phone LIKE :like OR s.phone_ext LIKE :like OR s.mobile LIKE :like)")
        elif "@" in q and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", q):
            where.append("s.email = :q")
            params["q"] = q
        else:
            where.append("(s.email LIKE :like OR s.lastname LIKE :like OR s.firstname LIKE :like)")
        params["like"] = _like(q)
    if dept_id:
        where.append("s.dept_id = :dept_id")
        params["dept_id"] = dept_id
    columns = _DIRECTORY_SORTS.get(sort) or sort_columns(name_format, "s.")
    order = ", ".join(f"{c} {'DESC' if descending else 'ASC'}" for c in columns)
    base = "FROM ost_staff s LEFT JOIN ost_department d ON d.id = s.dept_id WHERE " + " AND ".join(where)
    total = (await session.execute(_sql("SELECT COUNT(*) " + base), params)).scalar() or 0
    sql = ("SELECT s.staff_id AS id, s.firstname, s.lastname, s.email, COALESCE(s.phone, '') AS phone, "
           "COALESCE(s.phone_ext, '') AS phone_ext, COALESCE(s.mobile, '') AS mobile, "
           "s.dept_id, COALESCE(d.name, '') AS dept_name " + base + f" ORDER BY {order} LIMIT :limit OFFSET :offset")
    result = await session.execute(_sql(sql), params)
    return list(result.mappings().all()), int(total)


async def list_assignable_agents(session: AsyncSession, *, access: "Scope", dept_id: int, name_format: str = "full") -> list:
    """The agents offered in an assign form for an object of department ``dept_id``: ``Dept::getAssignees()`` —
    active agents not on vacation; everyone unless the department limits assignment to its members (primary and
    extra-access agents and the manager) or to its primary members — narrowed by ``Staff::applyDeptVisibility()``."""
    dept = await get_department(session, dept_id)
    if dept is None:
        return []
    flags = int(dept["flags"] or 0)
    where = ["s.isactive = 1", "s.onvacation = 0", _agent_visibility(access)]
    params: dict = {"dept_id": dept_id, "manager_id": int(dept["manager_id"] or 0)}
    if flags & 0x0001 or flags & 0x0010:   # FLAG_ASSIGN_MEMBERS_ONLY / FLAG_ASSIGN_PRIMARY_ONLY
        where.append("(s.dept_id = :dept_id OR (s.staff_id = :manager_id AND :manager_id > 0) OR EXISTS ("
                     "SELECT 1 FROM ost_staff_dept_access da WHERE da.staff_id = s.staff_id AND da.dept_id = :dept_id))")
        if flags & 0x0010:
            where.append("s.dept_id = :dept_id")
    order = ", ".join(sort_columns(name_format, "s."))
    sql = f"SELECT s.staff_id AS id, s.firstname, s.lastname FROM ost_staff s WHERE {' AND '.join(where)} ORDER BY {order}"
    result = await session.execute(_sql(sql), params)
    return list(result.mappings().all())


async def list_assignable_teams(session: AsyncSession, *, access: "Scope") -> list:
    """``Team::getActiveTeams()``: enabled teams with at least one active member who is not on vacation.
    osTicket offers the same teams to every agent that may assign."""
    sql = """
        SELECT t.team_id AS id, t.name FROM ost_team t
        WHERE (t.flags & 1) > 0 AND EXISTS (
            SELECT 1 FROM ost_team_member m JOIN ost_staff s ON s.staff_id = m.staff_id
            WHERE m.team_id = t.team_id AND s.isactive = 1 AND s.onvacation = 0)
        ORDER BY t.name ASC
    """
    result = await session.execute(_sql(sql))
    return list(result.mappings().all())


# ── Help Topics ───────────────────────────────────────────────────────────────

async def list_topics(session: AsyncSession, limit: int = 50, offset: int = 0) -> list:
    sql = """
        SELECT
            ht.topic_id AS id,
            ht.topic,
            (COALESCE(ht.flags, 0) & 2) > 0 AS is_active,   -- Topic::FLAG_ACTIVE
            ht.ispublic > 0 AS is_public,
            ht.created,
            ht.updated
        FROM ost_help_topic ht
        ORDER BY ht.topic ASC LIMIT :limit OFFSET :offset
    """
    result = await session.execute(_sql(sql), {"limit": limit, "offset": offset})
    return list(result.mappings().all())


async def list_topic_rows(session: AsyncSession) -> list:
    """Every help topic in osTicket's own order, with what ``Topic::getHelpTopics()`` reads — the caller
    applies osTicket's name and visibility rules (``Staff::getTopicNames()``), which are procedural."""
    sql = """
        SELECT
            ht.topic_id, ht.topic_pid, ht.ispublic, COALESCE(ht.flags, 0) AS flags,
            ht.topic, ht.dept_id, ht.created, ht.updated
        FROM ost_help_topic ht
        ORDER BY ht.sort ASC, ht.topic_id ASC
    """
    result = await session.execute(_sql(sql))
    return list(result.mappings().all())


async def get_topic(session: AsyncSession, topic_id: int) -> dict | None:
    sql = """
        SELECT
            ht.topic_id AS id,
            ht.topic,
            (COALESCE(ht.flags, 0) & 2) > 0 AS is_active,   -- Topic::FLAG_ACTIVE
            ht.ispublic > 0 AS is_public,
            ht.created,
            ht.updated,
            ht.topic_pid                AS parent_id,
            ht.dept_id,
            ht.priority_id,
            ht.status_id,
            ht.sla_id,
            ht.page_id,
            ht.noautoresp               AS no_auto_response,
            ht.sequence_id,
            COALESCE(ht.number_format, '') AS number_format,
            ht.staff_id                 AS assign_staff_id,
            ht.team_id                  AS assign_team_id,
            COALESCE(ht.flags, 0) AS flags,
            COALESCE(ht.notes, '') AS notes
        FROM ost_help_topic ht
        WHERE ht.topic_id = :topic_id
    """
    result = await session.execute(_sql(sql), {"topic_id": topic_id})
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def get_topic_form_ids(session: AsyncSession, topic_id: int) -> list[int]:
    """Forms attached to a help topic, in their order (ost_help_topic_form.sort)."""
    result = await session.execute(
        _sql("SELECT form_id FROM ost_help_topic_form WHERE topic_id = :topic_id ORDER BY sort, id"),
        {"topic_id": topic_id},
    )
    return [int(r[0]) for r in result.all()]


# ── SLAs (Service Level Agreements) ───────────────────────────────────────────

async def list_slas(session: AsyncSession, limit: int = 50, offset: int = 0) -> list:
    sql = """
        SELECT
            s.id,
            s.name,
            s.grace_period,
            (s.flags & 1) > 0 AS is_active,   -- SLA::FLAG_ACTIVE
            s.created,
            s.updated
        FROM ost_sla s
        ORDER BY s.name ASC LIMIT :limit OFFSET :offset
    """
    result = await session.execute(_sql(sql), {"limit": limit, "offset": offset})
    return list(result.mappings().all())


async def get_sla(session: AsyncSession, sla_id: int) -> dict | None:
    sql = """
        SELECT
            s.id,
            s.name,
            s.grace_period,
            (s.flags & 1) > 0 AS is_active,   -- SLA::FLAG_ACTIVE
            s.created,
            s.updated,
            s.schedule_id,
            (s.flags & 8) > 0 AS transient,                 -- SLA::FLAG_TRANSIENT
            (s.flags & 4) > 0 AS disable_overdue_alerts,    -- SLA::FLAG_NOALERTS
            COALESCE(s.notes, '') AS notes
        FROM ost_sla s
        WHERE s.id = :sla_id
    """
    result = await session.execute(_sql(sql), {"sla_id": sla_id})
    row = result.mappings().one_or_none()
    return dict(row) if row else None


# ── Priorities ────────────────────────────────────────────────────────────────

async def list_priorities(session: AsyncSession, limit: int = 50, offset: int = 0) -> list:
    sql = """
        SELECT
            p.priority_id AS id,
            p.priority AS name,
            p.priority_desc AS `desc`,
            p.priority_urgency AS urgency,
            p.ispublic > 0 AS is_public
        FROM ost_ticket_priority p
        ORDER BY p.priority_urgency ASC LIMIT :limit OFFSET :offset
    """
    result = await session.execute(_sql(sql), {"limit": limit, "offset": offset})
    return list(result.mappings().all())


async def get_priority(session: AsyncSession, priority_id: int) -> dict | None:
    sql = """
        SELECT
            p.priority_id AS id,
            p.priority AS name,
            p.priority_desc AS `desc`,
            p.priority_urgency AS urgency,
            p.ispublic > 0 AS is_public
        FROM ost_ticket_priority p
        WHERE p.priority_id = :priority_id
    """
    result = await session.execute(_sql(sql), {"priority_id": priority_id})
    row = result.mappings().one_or_none()
    return dict(row) if row else None


# ── KB Categories ─────────────────────────────────────────────────────────────

async def list_categories(session: AsyncSession, limit: int = 50, offset: int = 0) -> list:
    sql = """
        SELECT
            c.category_id AS id,
            c.name,
            COALESCE(c.description, '') AS description,
            (c.ispublic > 0) AS is_public,   -- 2 = featured
            c.created,
            c.updated
        FROM ost_faq_category c
        ORDER BY c.name ASC LIMIT :limit OFFSET :offset
    """
    result = await session.execute(_sql(sql), {"limit": limit, "offset": offset})
    return list(result.mappings().all())

async def get_category(session: AsyncSession, category_id: int) -> dict | None:
    sql = """
        SELECT
            c.category_id AS id,
            c.name,
            COALESCE(c.description, '') AS description,
            (c.ispublic > 0) AS is_public,   -- 2 = featured
            c.created,
            c.updated,
            COALESCE(c.notes, '') AS notes
        FROM ost_faq_category c
        WHERE c.category_id = :category_id
    """
    result = await session.execute(_sql(sql), {"category_id": category_id})
    row = result.mappings().one_or_none()
    return dict(row) if row else None


# ── FAQs ──────────────────────────────────────────────────────────────────────

async def list_faqs(
    session: AsyncSession,
    *,
    category_id: int | None = None,
    limit: int = 50,
    offset: int = 0
) -> list:
    where_clause = " WHERE f.category_id = :category_id" if category_id else ""
    params = {"limit": limit, "offset": offset}
    if category_id:
        params["category_id"] = category_id

    sql = f"""
        SELECT
            f.faq_id AS id,
            f.category_id,
            f.question,
            (f.ispublished > 0) AS is_published,   -- 2 = featured
            f.created,
            f.updated
        FROM ost_faq f
        {where_clause}
        ORDER BY f.question ASC LIMIT :limit OFFSET :offset
    """
    result = await session.execute(_sql(sql), params)
    return list(result.mappings().all())

async def get_faq(session: AsyncSession, faq_id: int) -> dict | None:
    sql = """
        SELECT
            f.faq_id AS id,
            f.category_id,
            f.question,
            COALESCE(f.answer, '') AS answer,
            (f.ispublished > 0) AS is_published,   -- 2 = featured
            COALESCE(f.keywords, '') AS keywords,
            COALESCE(f.notes, '') AS notes,
            f.created,
            f.updated
        FROM ost_faq f
        WHERE f.faq_id = :faq_id
    """
    result = await session.execute(_sql(sql), {"faq_id": faq_id})
    row = result.mappings().one_or_none()
    return dict(row) if row else None


# ── Canned Responses ──────────────────────────────────────────────────────────

def canned_visibility(access: "Scope", *, manage: bool) -> str:
    """SQL condition over ``ost_canned_response c``.

    ``manage``  the management page (``include/staff/cannedresponses.inc.php``): global responses plus those of
                departments where one of the agent's roles grants ``canned.manage`` — enabled or not.
    otherwise   the reply picker (``Canned::getCannedResponses``): enabled responses that are global or belong
                to one of the agent's departments.
    """
    if getattr(access, "unrestricted", False):
        return "1 = 1"
    if manage:
        return f"(c.dept_id = 0 OR {_in('c.dept_id', access.depts_granting('canned.manage'))})"
    return f"(c.isenabled = 1 AND (c.dept_id = 0 OR {_in('c.dept_id', access.dept_ids)}))"


async def list_canned(
    session: AsyncSession, *, access: "Scope", manage: bool, limit: int = 50, offset: int = 0
) -> list:
    sql = """
        SELECT
            c.canned_id AS id,
            c.title,
            c.isenabled AS is_enabled,
            c.dept_id,
            c.created,
            c.updated
        FROM ost_canned_response c
        WHERE {visible}
        ORDER BY c.title ASC LIMIT :limit OFFSET :offset
    """.replace("{visible}", canned_visibility(access, manage=manage))
    result = await session.execute(_sql(sql), {"limit": limit, "offset": offset})
    return list(result.mappings().all())

async def get_canned(
    session: AsyncSession, canned_id: int, *, access: "Scope", manage: bool
) -> dict | None:
    sql = """
        SELECT
            c.canned_id AS id,
            c.title,
            COALESCE(c.response, '') AS response,
            c.isenabled > 0 AS is_enabled,
            c.dept_id,
            COALESCE(c.notes, '') AS notes,
            c.created,
            c.updated
        FROM ost_canned_response c
        WHERE c.canned_id = :canned_id AND {visible}
    """.replace("{visible}", canned_visibility(access, manage=manage))
    result = await session.execute(_sql(sql), {"canned_id": canned_id})
    row = result.mappings().one_or_none()
    return dict(row) if row else None


# ── Banlist ───────────────────────────────────────────────────────────────────

# osTicket has no banlist table: the ban list is the "SYSTEM BAN LIST" ticket filter and each
# banned address is one of its email rules (what='email'). `id` is the filter rule id.
# osTicket's own installer sample rule has created = '0000-00-00 00:00:00', which is not a
# parseable datetime; fall back to `updated`.
_BAN_CREATED = "COALESCE(NULLIF(r.created, '0000-00-00 00:00:00'), r.updated)"

_BAN_RULES = """
    FROM ost_filter_rule r
    JOIN ost_filter f ON f.id = r.filter_id AND f.name = 'SYSTEM BAN LIST'
    WHERE r.what = 'email'
"""

async def list_banlist(session: AsyncSession, limit: int = 50, offset: int = 0) -> list:
    sql = (
        "SELECT r.id, r.val, r.isactive > 0 AS is_active, " + _BAN_CREATED + " AS created, r.updated"
        + _BAN_RULES
        + " ORDER BY r.val ASC LIMIT :limit OFFSET :offset"
    )
    result = await session.execute(_sql(sql), {"limit": limit, "offset": offset})
    return list(result.mappings().all())

async def get_banlist_entry(session: AsyncSession, ban_id: int) -> dict | None:
    sql = (
        "SELECT r.id, r.val, r.isactive > 0 AS is_active, COALESCE(r.notes, '') AS notes, " + _BAN_CREATED + " AS created, r.updated"
        + _BAN_RULES
        + " AND r.id = :ban_id"
    )
    result = await session.execute(_sql(sql), {"ban_id": ban_id})
    row = result.mappings().one_or_none()
    return dict(row) if row else None


# ── Forms ─────────────────────────────────────────────────────────────────────

async def list_forms(session: AsyncSession, limit: int = 50, offset: int = 0) -> list:
    sql = """
        SELECT
            f.id,
            f.title,
            f.type,
            (f.flags & 1) > 0 AS is_deletable,   -- DynamicForm::FLAG_DELETABLE
            f.created,
            f.updated
        FROM ost_form f
        WHERE (f.flags & 2) = 0                  -- DynamicForm::FLAG_DELETED is a soft delete
        ORDER BY f.title ASC LIMIT :limit OFFSET :offset
    """
    result = await session.execute(_sql(sql), {"limit": limit, "offset": offset})
    return list(result.mappings().all())

async def get_form(session: AsyncSession, form_id: int) -> dict | None:
    sql = """
        SELECT
            f.id,
            f.title,
            f.type,
            (f.flags & 1) > 0 AS is_deletable,   -- DynamicForm::FLAG_DELETABLE
            COALESCE(f.instructions, '') AS instructions,
            COALESCE(f.notes, '') AS notes,
            f.created,
            f.updated
        FROM ost_form f
        WHERE f.id = :form_id AND (f.flags & 2) = 0
    """
    result = await session.execute(_sql(sql), {"form_id": form_id})
    row = result.mappings().one_or_none()
    return dict(row) if row else None

async def get_form_fields(session: AsyncSession, form_id: int) -> list:
    sql = """
        SELECT
            ff.id,
            ff.name,
            ff.label,
            ff.type,
            (COALESCE(ff.flags, 0) & (0x4000 | 0x400)) > 0 AS is_required,   -- agent or client required
            ff.sort,
            COALESCE(ff.hint, '') AS hint,
            COALESCE(ff.flags, 0) AS flags,
            ff.configuration
        FROM ost_form_field ff
        WHERE ff.form_id = :form_id
        ORDER BY ff.sort ASC, ff.id ASC
    """
    result = await session.execute(_sql(sql), {"form_id": form_id})
    return list(result.mappings().all())


# ── Commands ──────────────────────────────────────────────────────────────────

def own_commands(access: "Scope") -> str:
    """SQL condition over ``ost_integration_inbox``: the commands this agent queued (all of them for UNRESTRICTED)."""
    if getattr(access, "unrestricted", False):
        return "1 = 1"
    return f"requested_by_staff_id = {int(access.staff_id)}"


async def list_commands(
    session: AsyncSession, *, access: "Scope", limit: int = 50, offset: int = 0
) -> list:
    sql = """
        SELECT
            id,
            command_type,
            aggregate_type,
            aggregate_id,
            status,
            requested_by_staff_id,
            created_at,
            processed_at
        FROM ost_integration_inbox
        WHERE {own}
        ORDER BY created_at DESC LIMIT :limit OFFSET :offset
    """.replace("{own}", own_commands(access))
    result = await session.execute(_sql(sql), {"limit": limit, "offset": offset})
    return list(result.mappings().all())

async def get_command(session: AsyncSession, command_id: int, *, access: "Scope") -> dict | None:
    sql = """
        SELECT
            id,
            command_type,
            aggregate_type,
            aggregate_id,
            payload,
            idempotency_key,
            status,
            requested_by_staff_id,
            result_payload,
            error_message,
            created_at,
            processed_at
        FROM ost_integration_inbox
        WHERE id = :command_id AND {own}
    """.replace("{own}", own_commands(access))
    result = await session.execute(_sql(sql), {"command_id": command_id})
    row = result.mappings().one_or_none()
    return dict(row) if row else None


# ── Search ────────────────────────────────────────────────────────────────────

async def search_all(
    session: AsyncSession,
    query: str,
    limit: int = 10,
    *,
    access: "Scope",
    include_directory: bool,
) -> list:
    """Multi-table search using UNION ALL.

    Tickets are narrowed to what ``access`` may see (``UNRESTRICTED`` = osTicket's ``search.all``).
    Users and organizations are the directory and appear only when ``include_directory`` (``user.dir``).
    """
    sql = """
        SELECT
            t.ticket_id AS id,
            'ticket' AS type,
            t.number AS title,
            COALESCE(c.subject, '') AS subtitle
        FROM ost_ticket t
        JOIN ost_ticket_status ts ON ts.id = t.status_id
        -- the subject is an answer of the ticket form, mirrored in ost_ticket__cdata
        LEFT JOIN ost_ticket__cdata c ON c.ticket_id = t.ticket_id
        WHERE (t.number LIKE :q OR c.subject LIKE :q) AND {ticket_visibility}
    """.replace("{ticket_visibility}", ticket_visibility(access, detail=False))
    if include_directory:
        sql += """
        UNION ALL

        SELECT
            u.id,
            'user' AS type,
            u.name AS title,
            COALESCE(ue.address, '') AS subtitle
        FROM ost_user u
        LEFT JOIN ost_user_email ue ON ue.id = u.default_email_id
        WHERE u.name LIKE :q OR ue.address LIKE :q

        UNION ALL

        SELECT
            id,
            'organization' AS type,
            name AS title,
            name AS subtitle
        FROM ost_organization
        WHERE name LIKE :q
        """
    sql += "\n        LIMIT :limit\n"
    result = await session.execute(_sql(sql), {"q": f"%{query}%", "limit": limit})
    return list(result.mappings().all())


# ── Organizations ─────────────────────────────────────────────────────────────


_ORG_SELECT = """
    SELECT
        o.id,
        o.name,
        o.created,
        COALESCE(o.updated, o.created) AS updated
    FROM ost_organization o
"""

async def list_organizations(
    session: AsyncSession, limit: int = 50, offset: int = 0
) -> list:
    sql = _ORG_SELECT + " ORDER BY o.name ASC LIMIT :limit OFFSET :offset"
    result = await session.execute(_sql(sql), {"limit": limit, "offset": offset})
    return list(result.mappings().all())

async def get_organization(session: AsyncSession, org_id: int) -> dict | None:
    # address / phone / website / notes are answers of the organization dynamic form;
    # osTicket keeps a denormalised copy of them in ost_organization__cdata.
    sql = """
        SELECT
            o.id,
            o.name,
            o.created,
            COALESCE(o.updated, o.created) AS updated,
            COALESCE(c.address, '') AS address,
            COALESCE(c.phone, '')   AS phone,
            COALESCE(c.website, '') AS website,
            COALESCE(c.notes, '')   AS internal_notes,
            COALESCE(o.manager, '') AS manager,
            COALESCE(o.domain, '')  AS domain,
            o.status
        FROM ost_organization o
        LEFT JOIN ost_organization__cdata c ON c.org_id = o.id
        WHERE o.id = :org_id
    """
    result = await session.execute(_sql(sql), {"org_id": org_id})
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def get_organization_primary_contacts(session: AsyncSession, org_id: int) -> list[int]:
    """Members flagged as primary contacts (User::PRIMARY_ORG_CONTACT, bit 1 of ost_user.status)."""
    result = await session.execute(
        _sql("SELECT id FROM ost_user WHERE org_id = :org_id AND (status & 1) > 0 ORDER BY id"), {"org_id": org_id}
    )
    return [int(r[0]) for r in result.all()]


# ── Users ─────────────────────────────────────────────────────────────────────

_USER_SELECT = """
    SELECT
        u.id,
        u.name,
        u.org_id,
        o.name AS org_name,
        u.status,
        u.created,
        u.updated,
        ue.address AS email
    FROM ost_user u
    LEFT JOIN ost_user_email ue ON ue.user_id = u.id
    LEFT JOIN ost_organization o ON o.id = u.org_id
"""

async def list_users(
    session: AsyncSession,
    *,
    org_id: int | None = None,
    limit: int = 50,
    offset: int = 0
) -> list:
    where_clause = " WHERE u.org_id = :org_id" if org_id is not None else ""
    params = {"limit": limit, "offset": offset}
    if org_id is not None:
        params["org_id"] = org_id
        
    sql = _USER_SELECT + where_clause + " ORDER BY u.name ASC LIMIT :limit OFFSET :offset"
    result = await session.execute(_sql(sql), params)
    return list(result.mappings().all())

async def get_user_account(session: AsyncSession, user_id: int) -> dict | None:
    """The end user's login account, or None before they register. Never selects the password hash."""
    sql = """
        SELECT username, COALESCE(timezone, '') AS timezone, status, registered
        FROM ost_user_account
        WHERE user_id = :user_id
    """
    result = await session.execute(_sql(sql), {"user_id": user_id})
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def get_user(session: AsyncSession, user_id: int) -> dict | None:
    # phone / notes are answers of the user dynamic form, mirrored by osTicket in
    # ost_user__cdata. The form has no mobile field, so that stays empty.
    sql = """
        SELECT
            u.id,
            u.name,
            u.org_id,
            o.name AS org_name,
            u.status,
            u.created,
            u.updated,
            ue.address AS email,
            COALESCE(c.phone, '') AS phone,
            ''                    AS mobile,
            COALESCE(c.notes, '') AS internal_notes
        FROM ost_user u
        LEFT JOIN ost_user_email ue ON ue.user_id = u.id
        LEFT JOIN ost_organization o ON o.id = u.org_id
        LEFT JOIN ost_user__cdata c ON c.user_id = u.id
        WHERE u.id = :user_id
    """
    result = await session.execute(_sql(sql), {"user_id": user_id})
    row = result.mappings().one_or_none()
    return dict(row) if row else None



# ── Tasks (top-level) ─────────────────────────────────────────────────────────


# ── Tasks (top-level) ─────────────────────────────────────────────────────────


_TASK_SELECT = """
    SELECT
        t.id                             AS task_id,
        t.number,
        COALESCE(c.title, '')            AS title,
        -- osTicket has no task status table: a task is open while flags bit 0x1 is set.
        -- Ids are synthetic (1 = open, 2 = closed) so the API model keeps its shape.
        IF(t.flags & 1, 1, 2)                AS status_id,
        IF(t.flags & 1, 'Open', 'Closed')    AS status_name,
        IF(t.flags & 1, 'open', 'closed')    AS status_state,
        d.id                             AS dept_id,
        d.name                           AS dept_name,
        IF(t.staff_id > 0, t.staff_id, NULL)                           AS assignee_staff_id,
        IF(t.staff_id > 0, CONCAT(s.firstname, ' ', s.lastname), NULL) AS assignee_name,
        IF(t.team_id > 0, t.team_id, NULL)   AS team_id,
        IF(t.team_id > 0, tm.name, NULL)     AS team_name,
        -- a task linked to a ticket carries the ticket as its parent object
        IF(t.object_type = 'T', t.object_id, NULL) AS ticket_id,
        t.created,
        t.updated,
        t.closed,
        t.duedate                        AS due_date
    FROM ost_task t
    LEFT JOIN ost_task__cdata c   ON c.task_id     = t.id
    LEFT JOIN ost_department d    ON t.dept_id     = d.id
    LEFT JOIN ost_staff s         ON t.staff_id    = s.staff_id AND t.staff_id > 0
    LEFT JOIN ost_team tm         ON t.team_id     = tm.team_id AND t.team_id > 0
"""


async def list_tasks(
    session: AsyncSession,
    *,
    access: "Scope",
    status_id: int | None = None,
    dept_id: int | None = None,
    assignee_staff_id: int | None = None,
    ticket_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list:
    where_clauses = [task_visibility(access, detail=False)]
    params: dict = {"limit": limit, "offset": offset}

    if status_id is not None:  # synthetic: 1 = open, anything else = closed
        where_clauses.append("(t.flags & 1) = :is_open")
        params["is_open"] = 1 if status_id == 1 else 0
    if dept_id is not None:
        where_clauses.append("t.dept_id = :dept_id")
        params["dept_id"] = dept_id
    if assignee_staff_id is not None:
        where_clauses.append("t.staff_id = :assignee_staff_id")
        params["assignee_staff_id"] = assignee_staff_id
    if ticket_id is not None:
        where_clauses.append("t.object_type = 'T' AND t.object_id = :ticket_id")
        params["ticket_id"] = ticket_id

    sql = (
        _TASK_SELECT
        + " WHERE " + " AND ".join(where_clauses)
        + " ORDER BY t.updated DESC"
        + " LIMIT :limit OFFSET :offset"
    )
    result = await session.execute(_sql(sql), params)
    return list(result.mappings().all())


async def get_task(session: AsyncSession, task_id: int, *, access: "Scope") -> dict | None:
    """The task row, or None if it does not exist *or the agent may not open it*."""
    sql = _TASK_SELECT + " WHERE t.id = :task_id AND " + task_visibility(access, detail=True)
    result = await session.execute(_sql(sql), {"task_id": task_id})
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def get_task_custom_fields(session: AsyncSession, task_id: int, *, access: "Scope") -> list:
    """Answers to the task's form fields that agents can see (nothing if the agent may not open the task)."""
    sql = _CUSTOM_FIELDS_SQL.replace("{visible}", _visible_task(access))
    result = await session.execute(_sql(sql), {"object_type": "A", "object_id": task_id, "task_id": task_id})
    return list(result.mappings().all())


async def get_user_custom_fields(session: AsyncSession, user_id: int) -> list:
    """Answers to the user form's fields that agents can see."""
    sql = _CUSTOM_FIELDS_SQL.replace("{visible}", "")
    result = await session.execute(_sql(sql), {"object_type": "U", "object_id": user_id})
    return list(result.mappings().all())


async def get_organization_custom_fields(session: AsyncSession, org_id: int) -> list:
    """Answers to the organization form's fields that agents can see."""
    sql = _CUSTOM_FIELDS_SQL.replace("{visible}", "")
    result = await session.execute(_sql(sql), {"object_type": "O", "object_id": org_id})
    return list(result.mappings().all())


async def get_task_thread(session: AsyncSession, task_id: int, *, access: "Scope") -> list:
    """Return thread entries for a task ordered by creation time (nothing if the agent may not open it)."""
    sql = """
        SELECT
            te.id,
            te.type,
            te.title,
            COALESCE(te.body, '') AS body,
            te.staff_id,
            te.user_id,
            te.ip_address,
            te.created,
            (te.flags & 2) > 0 AS is_edited,
            CASE
                WHEN te.staff_id > 0 THEN CONCAT(s.firstname, ' ', s.lastname)
                WHEN te.user_id  > 0 THEN u.name
                ELSE NULL
            END AS poster
        FROM ost_thread th
        JOIN ost_thread_entry te ON te.thread_id = th.id
        LEFT JOIN ost_staff s ON te.staff_id = s.staff_id AND te.staff_id > 0
        LEFT JOIN ost_user  u ON te.user_id  = u.id       AND te.user_id  > 0
        WHERE th.object_type = 'A' AND th.object_id = :task_id
          AND (te.flags & 4) = 0   -- FLAG_HIDDEN: the superseded version of an edited entry
        {visible}
        ORDER BY te.created ASC, te.id ASC
    """.replace("{visible}", _visible_task(access))
    result = await session.execute(_sql(sql), {"task_id": task_id})
    return list(result.mappings().all())
