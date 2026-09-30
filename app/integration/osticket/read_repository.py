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

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings

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
    result = await session.execute(_sql(sql), params)
    return list(result.mappings().all())


async def get_ticket(session: AsyncSession, ticket_id: int) -> dict | None:
    """Return a single ticket row dict, or None if not found."""
    sql = _TICKET_SELECT + " WHERE t.ticket_id = :ticket_id"
    result = await session.execute(_sql(sql), {"ticket_id": ticket_id})
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def get_ticket_thread(session: AsyncSession, ticket_id: int) -> list:
    """Return thread entries for a ticket ordered by creation time."""
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
        ORDER BY te.created ASC, te.id ASC
    """
    result = await session.execute(_sql(sql), {"ticket_id": ticket_id})
    return list(result.mappings().all())


async def get_ticket_collaborators(session: AsyncSession, ticket_id: int) -> list:
    """Return collaborators for a ticket."""
    sql = """
        SELECT
            c.id,
            c.user_id,
            u.name,
            COALESCE(ue.address, '') AS email
        FROM ost_thread_collaborator c
        JOIN ost_thread th ON th.id = c.thread_id AND th.object_type = 'T'
        JOIN ost_user u ON c.user_id = u.id
        LEFT JOIN ost_user_email ue ON ue.id = u.default_email_id
        WHERE th.object_id = :ticket_id
        ORDER BY c.id ASC
    """
    result = await session.execute(_sql(sql), {"ticket_id": ticket_id})
    return list(result.mappings().all())


# ── Staff ─────────────────────────────────────────────────────────────────────


async def get_staff_active(session: AsyncSession, staff_id: int) -> bool | None:
    """Whether the staff member exists and is enabled (None: no such staff member)."""
    row = (
        await session.execute(
            _sql("SELECT isactive FROM ost_staff WHERE staff_id = :sid"), {"sid": staff_id}
        )
    ).first()
    return None if row is None else bool(row[0])


async def get_staff_permissions(
    session: AsyncSession, staff_id: int
) -> tuple[list[str], bool]:
    """Return (permissions_list, is_admin) for a staff member.

    Called at login time to embed permissions into the JWT.
    """
    result = await session.execute(
        _sql("SELECT isadmin, role_id FROM ost_staff WHERE staff_id = :sid"),
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
        _sql("SELECT permissions FROM ost_role WHERE id = :rid"),
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
            s.phone,
            s.mobile
        FROM ost_staff s
        WHERE s.staff_id = :staff_id
    """
    result = await session.execute(_sql(sql), {"staff_id": staff_id})
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def get_staff_teams(session: AsyncSession, staff_id: int) -> list[int]:
    sql = "SELECT team_id FROM ost_team_member WHERE staff_id = :staff_id"
    result = await session.execute(_sql(sql), {"staff_id": staff_id})
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
            d.flags
        FROM ost_department d
        WHERE d.id = :dept_id
    """
    result = await session.execute(_sql(sql), {"dept_id": dept_id})
    row = result.mappings().one_or_none()
    return dict(row) if row else None


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
            COALESCE(t.notes, '') AS notes
        FROM ost_team t
        WHERE t.team_id = :team_id
    """
    result = await session.execute(_sql(sql), {"team_id": team_id})
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def get_team_members(session: AsyncSession, team_id: int) -> list[int]:
    sql = "SELECT staff_id FROM ost_team_member WHERE team_id = :team_id"
    result = await session.execute(_sql(sql), {"team_id": team_id})
    return [r["staff_id"] for r in result.mappings().all()]


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


async def get_topic(session: AsyncSession, topic_id: int) -> dict | None:
    sql = """
        SELECT
            ht.topic_id AS id,
            ht.topic,
            (COALESCE(ht.flags, 0) & 2) > 0 AS is_active,   -- Topic::FLAG_ACTIVE
            ht.ispublic > 0 AS is_public,
            ht.created,
            ht.updated,
            ht.dept_id,
            ht.priority_id,
            ht.sla_id,
            ht.page_id,
            COALESCE(ht.flags, 0) AS flags,
            COALESCE(ht.notes, '') AS notes
        FROM ost_help_topic ht
        WHERE ht.topic_id = :topic_id
    """
    result = await session.execute(_sql(sql), {"topic_id": topic_id})
    row = result.mappings().one_or_none()
    return dict(row) if row else None


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
            s.flags,
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
    result = await session.execute(_sql(sql), {"limit": limit, "offset": offset})
    return list(result.mappings().all())

async def get_canned(session: AsyncSession, canned_id: int) -> dict | None:
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
        WHERE c.canned_id = :canned_id
    """
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
            (COALESCE(ff.flags, 0) & (0x4000 | 0x400)) > 0 AS is_required   -- agent or client required
        FROM ost_form_field ff
        WHERE ff.form_id = :form_id
        ORDER BY ff.sort ASC
    """
    result = await session.execute(_sql(sql), {"form_id": form_id})
    return list(result.mappings().all())


# ── Commands ──────────────────────────────────────────────────────────────────

async def list_commands(session: AsyncSession, limit: int = 50, offset: int = 0) -> list:
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
        ORDER BY created_at DESC LIMIT :limit OFFSET :offset
    """
    result = await session.execute(_sql(sql), {"limit": limit, "offset": offset})
    return list(result.mappings().all())

async def get_command(session: AsyncSession, command_id: int) -> dict | None:
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
        WHERE id = :command_id
    """
    result = await session.execute(_sql(sql), {"command_id": command_id})
    row = result.mappings().one_or_none()
    return dict(row) if row else None


# ── Search ────────────────────────────────────────────────────────────────────

async def search_all(session: AsyncSession, query: str, limit: int = 10) -> list:
    """Basic multi-table search using UNION ALL."""
    sql = """
        SELECT
            t.ticket_id AS id,
            'ticket' AS type,
            t.number AS title,
            COALESCE(c.subject, '') AS subtitle
        FROM ost_ticket t
        -- the subject is an answer of the ticket form, mirrored in ost_ticket__cdata
        LEFT JOIN ost_ticket__cdata c ON c.ticket_id = t.ticket_id
        WHERE t.number LIKE :q OR c.subject LIKE :q

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

        LIMIT :limit
    """
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
            COALESCE(c.notes, '')   AS internal_notes
        FROM ost_organization o
        LEFT JOIN ost_organization__cdata c ON c.org_id = o.id
        WHERE o.id = :org_id
    """
    result = await session.execute(_sql(sql), {"org_id": org_id})
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
    result = await session.execute(_sql(sql), params)
    return list(result.mappings().all())

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
    status_id: int | None = None,
    dept_id: int | None = None,
    assignee_staff_id: int | None = None,
    ticket_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list:
    where_clauses = ["1=1"]
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


async def get_task(session: AsyncSession, task_id: int) -> dict | None:
    sql = _TASK_SELECT + " WHERE t.id = :task_id"
    result = await session.execute(_sql(sql), {"task_id": task_id})
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def get_task_thread(session: AsyncSession, task_id: int) -> list:
    """Return thread entries for a task ordered by creation time."""
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
        ORDER BY te.created ASC, te.id ASC
    """
    result = await session.execute(_sql(sql), {"task_id": task_id})
    return list(result.mappings().all())


async def list_tasks_from_osticket(
    session: AsyncSession, limit: int = 50, offset: int = 0
) -> list:
    """Backward-compat alias; prefer list_tasks() directly."""
    return await list_tasks(session, limit=limit, offset=offset)

