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
    result = await session.execute(text(sql), {"limit": limit, "offset": offset})
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
            s.phone,
            s.mobile
        FROM ost_staff s
        WHERE s.staff_id = :staff_id
    """
    result = await session.execute(text(sql), {"staff_id": staff_id})
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def get_staff_teams(session: AsyncSession, staff_id: int) -> list[int]:
    sql = "SELECT team_id FROM ost_team_member WHERE staff_id = :staff_id"
    result = await session.execute(text(sql), {"staff_id": staff_id})
    return [r["team_id"] for r in result.mappings().all()]


# ── Departments ───────────────────────────────────────────────────────────────

async def list_departments(session: AsyncSession, limit: int = 50, offset: int = 0) -> list:
    sql = """
        SELECT
            d.id,
            d.name,
            d.ispublic AS is_public,
            d.created,
            d.updated
        FROM ost_department d
        ORDER BY d.name ASC LIMIT :limit OFFSET :offset
    """
    result = await session.execute(text(sql), {"limit": limit, "offset": offset})
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
            d.flags
        FROM ost_department d
        WHERE d.id = :dept_id
    """
    result = await session.execute(text(sql), {"dept_id": dept_id})
    row = result.mappings().one_or_none()
    return dict(row) if row else None


# ── Teams ─────────────────────────────────────────────────────────────────────

async def list_teams(session: AsyncSession, limit: int = 50, offset: int = 0) -> list:
    sql = """
        SELECT
            t.team_id AS id,
            t.name,
            t.isenabled AS is_enabled,
            t.created,
            t.updated
        FROM ost_team t
        ORDER BY t.name ASC LIMIT :limit OFFSET :offset
    """
    result = await session.execute(text(sql), {"limit": limit, "offset": offset})
    return list(result.mappings().all())


async def get_team(session: AsyncSession, team_id: int) -> dict | None:
    sql = """
        SELECT
            t.team_id AS id,
            t.name,
            t.isenabled AS is_enabled,
            t.created,
            t.updated,
            t.lead_id,
            t.notes
        FROM ost_team t
        WHERE t.team_id = :team_id
    """
    result = await session.execute(text(sql), {"team_id": team_id})
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def get_team_members(session: AsyncSession, team_id: int) -> list[int]:
    sql = "SELECT staff_id FROM ost_team_member WHERE team_id = :team_id"
    result = await session.execute(text(sql), {"team_id": team_id})
    return [r["staff_id"] for r in result.mappings().all()]


# ── Help Topics ───────────────────────────────────────────────────────────────

async def list_topics(session: AsyncSession, limit: int = 50, offset: int = 0) -> list:
    sql = """
        SELECT
            ht.topic_id AS id,
            ht.topic,
            ht.isactive AS is_active,
            ht.ispublic AS is_public,
            ht.created,
            ht.updated
        FROM ost_help_topic ht
        ORDER BY ht.topic ASC LIMIT :limit OFFSET :offset
    """
    result = await session.execute(text(sql), {"limit": limit, "offset": offset})
    return list(result.mappings().all())


async def get_topic(session: AsyncSession, topic_id: int) -> dict | None:
    sql = """
        SELECT
            ht.topic_id AS id,
            ht.topic,
            ht.isactive AS is_active,
            ht.ispublic AS is_public,
            ht.created,
            ht.updated,
            ht.dept_id,
            ht.priority_id,
            ht.sla_id,
            ht.page_id,
            ht.flags,
            ht.notes
        FROM ost_help_topic ht
        WHERE ht.topic_id = :topic_id
    """
    result = await session.execute(text(sql), {"topic_id": topic_id})
    row = result.mappings().one_or_none()
    return dict(row) if row else None


# ── SLAs (Service Level Agreements) ───────────────────────────────────────────

async def list_slas(session: AsyncSession, limit: int = 50, offset: int = 0) -> list:
    sql = """
        SELECT
            s.id,
            s.name,
            s.grace_period,
            s.isactive AS is_active,
            s.created,
            s.updated
        FROM ost_sla s
        ORDER BY s.name ASC LIMIT :limit OFFSET :offset
    """
    result = await session.execute(text(sql), {"limit": limit, "offset": offset})
    return list(result.mappings().all())


async def get_sla(session: AsyncSession, sla_id: int) -> dict | None:
    sql = """
        SELECT
            s.id,
            s.name,
            s.grace_period,
            s.isactive AS is_active,
            s.created,
            s.updated,
            s.flags,
            s.notes
        FROM ost_sla s
        WHERE s.id = :sla_id
    """
    result = await session.execute(text(sql), {"sla_id": sla_id})
    row = result.mappings().one_or_none()
    return dict(row) if row else None


# ── Priorities ────────────────────────────────────────────────────────────────

async def list_priorities(session: AsyncSession, limit: int = 50, offset: int = 0) -> list:
    sql = """
        SELECT
            p.priority_id AS id,
            p.priority_desc AS name,
            p.priority_desc AS desc,
            p.priority_urgency AS urgency,
            p.ispublic AS is_public
        FROM ost_ticket_priority p
        ORDER BY p.priority_urgency ASC LIMIT :limit OFFSET :offset
    """
    result = await session.execute(text(sql), {"limit": limit, "offset": offset})
    return list(result.mappings().all())


async def get_priority(session: AsyncSession, priority_id: int) -> dict | None:
    sql = """
        SELECT
            p.priority_id AS id,
            p.priority_desc AS name,
            p.priority_desc AS desc,
            p.priority_urgency AS urgency,
            p.ispublic AS is_public
        FROM ost_ticket_priority p
        WHERE p.priority_id = :priority_id
    """
    result = await session.execute(text(sql), {"priority_id": priority_id})
    row = result.mappings().one_or_none()
    return dict(row) if row else None


# ── KB Categories ─────────────────────────────────────────────────────────────

async def list_categories(session: AsyncSession, limit: int = 50, offset: int = 0) -> list:
    sql = """
        SELECT
            c.category_id AS id,
            c.name,
            c.description,
            c.ispublic AS is_public,
            c.created,
            c.updated
        FROM ost_faq_category c
        ORDER BY c.name ASC LIMIT :limit OFFSET :offset
    """
    result = await session.execute(text(sql), {"limit": limit, "offset": offset})
    return list(result.mappings().all())

async def get_category(session: AsyncSession, category_id: int) -> dict | None:
    sql = """
        SELECT
            c.category_id AS id,
            c.name,
            c.description,
            c.ispublic AS is_public,
            c.created,
            c.updated,
            c.notes
        FROM ost_faq_category c
        WHERE c.category_id = :category_id
    """
    result = await session.execute(text(sql), {"category_id": category_id})
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
            f.ispublished AS is_published,
            f.created,
            f.updated
        FROM ost_faq f
        {where_clause}
        ORDER BY f.question ASC LIMIT :limit OFFSET :offset
    """
    result = await session.execute(text(sql), params)
    return list(result.mappings().all())

async def get_faq(session: AsyncSession, faq_id: int) -> dict | None:
    sql = """
        SELECT
            f.faq_id AS id,
            f.category_id,
            f.question,
            f.answer,
            f.ispublished AS is_published,
            f.keywords,
            f.notes,
            f.created,
            f.updated
        FROM ost_faq f
        WHERE f.faq_id = :faq_id
    """
    result = await session.execute(text(sql), {"faq_id": faq_id})
    row = result.mappings().one_or_none()
    return dict(row) if row else None


# ── Canned Responses ──────────────────────────────────────────────────────────

async def list_canned(session: AsyncSession, limit: int = 50, offset: int = 0) -> list:
    sql = """
        SELECT
            c.canned_id AS id,
            c.title,
            c.isenabled AS is_enabled,
            c.dept_id,
            c.created,
            c.updated
        FROM ost_canned_response c
        ORDER BY c.title ASC LIMIT :limit OFFSET :offset
    """
    result = await session.execute(text(sql), {"limit": limit, "offset": offset})
    return list(result.mappings().all())

async def get_canned(session: AsyncSession, canned_id: int) -> dict | None:
    sql = """
        SELECT
            c.canned_id AS id,
            c.title,
            c.response,
            c.isenabled AS is_enabled,
            c.dept_id,
            c.notes,
            c.created,
            c.updated
        FROM ost_canned_response c
        WHERE c.canned_id = :canned_id
    """
    result = await session.execute(text(sql), {"canned_id": canned_id})
    row = result.mappings().one_or_none()
    return dict(row) if row else None


# ── Banlist ───────────────────────────────────────────────────────────────────

async def list_banlist(session: AsyncSession, limit: int = 50, offset: int = 0) -> list:
    sql = """
        SELECT
            b.id,
            b.val,
            b.isactive AS is_active,
            b.created,
            b.updated
        FROM ost_ticket_banlist b
        ORDER BY b.val ASC LIMIT :limit OFFSET :offset
    """
    result = await session.execute(text(sql), {"limit": limit, "offset": offset})
    return list(result.mappings().all())

async def get_banlist_entry(session: AsyncSession, ban_id: int) -> dict | None:
    sql = """
        SELECT
            b.id,
            b.val,
            b.isactive AS is_active,
            b.notes,
            b.created,
            b.updated
        FROM ost_ticket_banlist b
        WHERE b.id = :ban_id
    """
    result = await session.execute(text(sql), {"ban_id": ban_id})
    row = result.mappings().one_or_none()
    return dict(row) if row else None


# ── Forms ─────────────────────────────────────────────────────────────────────

async def list_forms(session: AsyncSession, limit: int = 50, offset: int = 0) -> list:
    sql = """
        SELECT
            f.id,
            f.title,
            f.type,
            f.deletable AS is_deletable,
            f.created,
            f.updated
        FROM ost_form f
        ORDER BY f.title ASC LIMIT :limit OFFSET :offset
    """
    result = await session.execute(text(sql), {"limit": limit, "offset": offset})
    return list(result.mappings().all())

async def get_form(session: AsyncSession, form_id: int) -> dict | None:
    sql = """
        SELECT
            f.id,
            f.title,
            f.type,
            f.deletable AS is_deletable,
            f.instructions,
            f.notes,
            f.created,
            f.updated
        FROM ost_form f
        WHERE f.id = :form_id
    """
    result = await session.execute(text(sql), {"form_id": form_id})
    row = result.mappings().one_or_none()
    return dict(row) if row else None

async def get_form_fields(session: AsyncSession, form_id: int) -> list:
    sql = """
        SELECT
            ff.id,
            ff.name,
            ff.label,
            ff.type,
            ff.isrequired AS is_required
        FROM ost_form_field ff
        WHERE ff.form_id = :form_id
        ORDER BY ff.sort ASC
    """
    result = await session.execute(text(sql), {"form_id": form_id})
    return list(result.mappings().all())


# ── Search ────────────────────────────────────────────────────────────────────

async def search_all(session: AsyncSession, query: str, limit: int = 10) -> list:
    """Basic multi-table search using UNION ALL."""
    sql = """
        SELECT
            ticket_id AS id,
            'ticket' AS type,
            number AS title,
            subject AS subtitle
        FROM ost_ticket
        WHERE number LIKE :q OR subject LIKE :q
        
        UNION ALL
        
        SELECT
            id,
            'user' AS type,
            name AS title,
            email AS subtitle
        FROM ost_user
        WHERE name LIKE :q OR email LIKE :q
        
        UNION ALL
        
        SELECT
            id,
            'organization' AS type,
            name AS title,
            name AS subtitle
        FROM ost_organization
        WHERE name LIKE :q
        
        LIMIT :limit
    """
    result = await session.execute(text(sql), {"q": f"%{query}%", "limit": limit})
    return list(result.mappings().all())


# ── Organizations ─────────────────────────────────────────────────────────────


_ORG_SELECT = """
    SELECT
        o.id,
        o.name,
        o.created,
        o.updated
    FROM ost_organization o
"""

async def list_organizations(
    session: AsyncSession, limit: int = 50, offset: int = 0
) -> list:
    sql = _ORG_SELECT + " ORDER BY o.name ASC LIMIT :limit OFFSET :offset"
    result = await session.execute(text(sql), {"limit": limit, "offset": offset})
    return list(result.mappings().all())

async def get_organization(session: AsyncSession, org_id: int) -> dict | None:
    sql = """
        SELECT
            o.id,
            o.name,
            o.created,
            o.updated,
            -- Extra fields come from ost_organization__cdata or forms,
            -- for now we return placeholder strings to match the model
            '' AS address,
            '' AS phone,
            '' AS website,
            '' AS internal_notes
        FROM ost_organization o
        WHERE o.id = :org_id
    """
    result = await session.execute(text(sql), {"org_id": org_id})
    row = result.mappings().one_or_none()
    return dict(row) if row else None


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
    result = await session.execute(text(sql), params)
    return list(result.mappings().all())

async def get_user(session: AsyncSession, user_id: int) -> dict | None:
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
            -- Extra fields like phone, mobile are in user__cdata or forms,
            -- keeping placeholders for now
            '' AS phone,
            '' AS mobile,
            '' AS internal_notes
        FROM ost_user u
        LEFT JOIN ost_user_email ue ON ue.user_id = u.id
        LEFT JOIN ost_organization o ON o.id = u.org_id
        WHERE u.id = :user_id
    """
    result = await session.execute(text(sql), {"user_id": user_id})
    row = result.mappings().one_or_none()
    return dict(row) if row else None



# ── Tasks (top-level) ─────────────────────────────────────────────────────────


# ── Tasks (top-level) ─────────────────────────────────────────────────────────


_TASK_SELECT = """
    SELECT
        t.id                             AS task_id,
        t.number,
        t.title,
        ts.id                            AS status_id,
        ts.name                          AS status_name,
        ts.state                         AS status_state,
        d.id                             AS dept_id,
        d.name                           AS dept_name,
        IF(t.staff_id > 0, t.staff_id, NULL)                           AS assignee_staff_id,
        IF(t.staff_id > 0, CONCAT(s.firstname, ' ', s.lastname), NULL) AS assignee_name,
        IF(t.team_id > 0, t.team_id, NULL)   AS team_id,
        IF(t.team_id > 0, tm.name, NULL)     AS team_name,
        tl.object_id                     AS ticket_id,
        t.created,
        t.updated,
        t.closed,
        t.duedate                        AS due_date
    FROM ost_task t
    LEFT JOIN ost_task_status ts  ON t.status_id  = ts.id
    LEFT JOIN ost_department d    ON t.dept_id     = d.id
    LEFT JOIN ost_staff s         ON t.staff_id    = s.staff_id AND t.staff_id > 0
    LEFT JOIN ost_team tm         ON t.team_id     = tm.team_id AND t.team_id > 0
    -- ost_task_link maps tasks to parent objects (tickets, etc.)
    LEFT JOIN ost_task_link tl    ON tl.task_id    = t.id AND tl.object_type = 'T'
"""


async def list_tasks(
    session: AsyncSession,
    *,
    status_id: int | None = None,
    dept_id: int | None = None,
    assignee_staff_id: int | None = None,
    ticket_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list:
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
    if ticket_id is not None:
        where_clauses.append("tl.object_id = :ticket_id")
        params["ticket_id"] = ticket_id

    sql = (
        _TASK_SELECT
        + " WHERE " + " AND ".join(where_clauses)
        + " ORDER BY t.updated DESC"
        + " LIMIT :limit OFFSET :offset"
    )
    result = await session.execute(text(sql), params)
    return list(result.mappings().all())


async def get_task(session: AsyncSession, task_id: int) -> dict | None:
    sql = _TASK_SELECT + " WHERE t.id = :task_id"
    result = await session.execute(text(sql), {"task_id": task_id})
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def get_task_thread(session: AsyncSession, task_id: int) -> list:
    """Return thread entries for a task ordered by creation time."""
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
        FROM ost_task t
        JOIN ost_thread_entry te ON te.thread_id = t.thread_id
        LEFT JOIN ost_staff s ON te.staff_id = s.staff_id AND te.staff_id > 0
        LEFT JOIN ost_user  u ON te.user_id  = u.id       AND te.user_id  > 0
        WHERE t.id = :task_id
        ORDER BY te.created ASC, te.id ASC
    """
    result = await session.execute(text(sql), {"task_id": task_id})
    return list(result.mappings().all())


async def list_tasks_from_osticket(
    session: AsyncSession, limit: int = 50, offset: int = 0
) -> list:
    """Backward-compat alias; prefer list_tasks() directly."""
    return await list_tasks(session, limit=limit, offset=offset)

