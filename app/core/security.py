"""Permission enforcement dependencies.

Every non-public route must use one of the dependencies defined here:

    require_perm("user.create")          # an osTicket permission (see app/core/permissions.py)
    require_perm("search.all", admin_ok=True)
    require_admin()                       # the Admin Panel: osTicket gates those pages on isAdmin()
    require_staff()                       # any enabled agent (reads whose visibility is scoped in the query)

What the caller may do is read from ``AccessContext`` (app/core/access.py), loaded from osTicket's own
tables on each request — not from the login token — and evaluated the way osTicket's ``Staff::hasPerm``
does. There is **no blanket admin bypass** in ``require_perm``: osTicket has none. An administrator's
power over the Admin Panel is ``require_admin()``; for everything else they hold the permissions they
were given.

``require_perm`` raises ``ValueError`` at import for a key osTicket does not have, so a guard that can
never be satisfied fails loudly at startup rather than quietly locking everyone out.
"""

from fastapi import Depends, HTTPException

from app.core.auth import get_current_user
from app.core.permissions import permission_kind


def require_perm(perm: str, *, admin_ok: bool = False):
    """Return a FastAPI dependency that enforces one osTicket permission.

    A *global* permission (``user.*``, ``org.*``, ``faq.manage``, ...) must be on the staff member's own
    permission map; a *role* permission (``ticket.*``, ``task.*``, ``canned.manage``, ...) must be granted
    by at least one of their roles. ``admin_ok=True`` additionally lets administrators through, for the
    few routes where osTicket's own behaviour for an admin without the permission is undefined.
    """
    kind = permission_kind(perm)   # fails at import for an unknown key

    async def _check(current_user: dict = Depends(get_current_user)) -> dict:
        access = current_user["access"]
        if admin_ok and access.is_admin:
            return current_user
        granted = access.has_global(perm) if kind == "global" else access.has_role_perm(perm)
        if not granted:
            raise HTTPException(status_code=403, detail=f"Permission required: {perm}")
        return current_user

    return Depends(_check)


def require_admin():
    """Dependency: the staff member must be an administrator (osTicket's ``isAdmin()``).

    For Admin Panel resources — staff, departments, teams, help topics, SLAs, priorities, forms,
    the banlist — which osTicket lets administrators manage and nobody else.
    """

    async def _check(current_user: dict = Depends(get_current_user)) -> dict:
        if not current_user["access"].is_admin:
            raise HTTPException(status_code=403, detail="Admin access required.")
        return current_user

    return Depends(_check)


def require_staff():
    """Dependency: any enabled agent.

    For reads where osTicket applies no permission at all (the knowledge base) or where visibility is
    narrowed by the query itself rather than by a permission (read scoping, S3).
    """

    async def _check(current_user: dict = Depends(get_current_user)) -> dict:
        return current_user

    return Depends(_check)
