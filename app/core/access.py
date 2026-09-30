"""What a staff member may do, computed from osTicket's own tables on every request.

``AccessContext`` mirrors the parts of osTicket's ``Staff`` class that decide access
(``include/class.staff.php``): ``isAdmin()``, ``hasPerm()``, ``getRoles()``/``getRole()``,
``getDepartments()``, ``getTeams()`` and ``isAccessLimited()``. It is loaded fresh per request rather
than trusted from the login token, so a role change, a demotion or a disabled account takes effect on
the next request. (Phase 4 puts a short cache in front of the loader.)

Nothing here decides what a *resource* looks like to the agent — that is read scoping (S3), which
uses ``dept_ids``, ``team_ids`` and ``assigned_only`` from this context.
"""

import json
from dataclasses import dataclass, field
from typing import Any, ClassVar

from sqlalchemy.ext.asyncio import AsyncSession

from app.integration.osticket import read_repository as repo

# The role osTicket gives an agent for a department they have no role in: create-and-view only.
FALLBACK_ROLE_PERMS = frozenset({"ticket.create"})


def _perm_set(raw: Any) -> frozenset[str]:
    """Keys switched on in a stored permission map (JSON text, NULL, or already a dict)."""
    if not raw:
        return frozenset()
    if isinstance(raw, (str, bytes)):
        try:
            raw = json.loads(raw)
        except ValueError:
            return frozenset()
    if not isinstance(raw, dict):
        return frozenset()
    return frozenset(key for key, value in raw.items() if value)


@dataclass(frozen=True)
class AccessContext:
    #: read_repository narrows queries for an AccessContext and leaves scope.UNRESTRICTED alone
    unrestricted: ClassVar[bool] = False

    staff_id: int
    username: str = ""
    name: str = ""
    email: str = ""
    is_active: bool = True
    is_admin: bool = False
    #: ``ost_staff.permissions`` — the global permissions (user.*, org.*, faq.manage, ...)
    global_perms: frozenset[str] = frozenset()
    primary_dept_id: int = 0
    #: department id -> the role's permissions there: the primary department plus each extra
    #: department in ost_staff_dept_access (as ``Staff::getRoles()``). Sub-departments inherit no role.
    dept_roles: dict[int, frozenset[str]] = field(default_factory=dict)
    #: departments the agent may see: primary, extra and managed departments and their sub-departments
    #: (as ``Staff::getDepartments()``)
    dept_ids: frozenset[int] = frozenset()
    team_ids: frozenset[int] = frozenset()
    #: "limited access" agents see only what is assigned to them or their teams
    assigned_only: bool = False

    # ── permission questions (osTicket's Staff::hasPerm) ─────────────────────

    def has_global(self, perm: str) -> bool:
        """``$staff->hasPerm($perm)`` — no admin bypass, exactly as osTicket."""
        return perm in self.global_perms

    def has_role_perm(self, perm: str) -> bool:
        """``$staff->hasPerm($perm, false)`` — any of the agent's roles grants it."""
        return any(perm in perms for perms in self.dept_roles.values())

    def role_perms_for(self, dept_id: int) -> frozenset[str]:
        """The role that applies in one department (``$staff->getRole($dept)``)."""
        return self.dept_roles.get(dept_id, FALLBACK_ROLE_PERMS)

    def can_role_perm_in(self, perm: str, dept_id: int) -> bool:
        return perm in self.role_perms_for(dept_id)

    # ── visibility ingredients (used by read scoping) ────────────────────────

    def can_access_dept(self, dept_id: int) -> bool:
        """``$staff->canAccessDept()``: limited-access agents never have department access."""
        return not self.assigned_only and dept_id in self.dept_ids

    def depts_granting(self, perm: str) -> frozenset[int]:
        """Departments in which one of the agent's roles grants ``perm`` (osTicket's canned-response
        management page lists only these, plus the global ones)."""
        return frozenset(dept for dept, perms in self.dept_roles.items() if perm in perms)

    def is_team_member(self, team_id: int | None) -> bool:
        return bool(team_id) and team_id in self.team_ids

    # ── presentation ──────────────────────────────────────────────────────────

    def all_permissions(self) -> list[str]:
        """Every permission the agent holds anywhere, for clients that want a flat list."""
        return sorted(self.global_perms | {p for perms in self.dept_roles.values() for p in perms})

    def summary(self) -> dict:
        return {
            "staff_id": self.staff_id,
            "username": self.username,
            "name": self.name,
            "email": self.email,
            "is_admin": self.is_admin,
            "assigned_only": self.assigned_only,
            "global_permissions": sorted(self.global_perms),
            "roles": {str(dept): sorted(perms) for dept, perms in sorted(self.dept_roles.items())},
            "department_ids": sorted(self.dept_ids),
            "team_ids": sorted(self.team_ids),
        }


def build_access_context(rows: dict) -> AccessContext:
    """Assemble a context from ``read_repository.get_staff_access_rows`` output (pure, testable)."""
    staff = rows["staff"]
    role_perms = {int(role_id): _perm_set(perms) for role_id, perms in rows["roles"].items()}

    primary_dept = int(staff["dept_id"] or 0)
    dept_roles = {primary_dept: role_perms.get(int(staff["role_id"] or 0), frozenset())}
    for extra in rows["dept_access"]:   # an extra row for the primary department replaces it, as in osTicket
        dept_roles[int(extra["dept_id"])] = role_perms.get(int(extra["role_id"] or 0), frozenset())

    name = " ".join(part for part in (staff.get("firstname"), staff.get("lastname")) if part).strip()
    return AccessContext(
        staff_id=int(staff["staff_id"]),
        username=staff.get("username") or "",
        name=name,
        email=staff.get("email") or "",
        is_active=bool(staff["isactive"]),
        is_admin=bool(staff["isadmin"]),
        global_perms=_perm_set(staff.get("permissions")),
        primary_dept_id=primary_dept,
        dept_roles=dept_roles,
        dept_ids=frozenset(int(d) for d in rows["dept_ids"]),
        team_ids=frozenset(int(t) for t in rows["team_ids"]),
        assigned_only=bool(staff["assigned_only"]),
    )


async def load_access_context(session: AsyncSession, staff_id: int) -> AccessContext | None:
    """The agent's current access, or None if there is no such staff member."""
    rows = await repo.get_staff_access_rows(session, staff_id)
    return build_access_context(rows) if rows else None
