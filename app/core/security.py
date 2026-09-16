"""Permission enforcement dependencies.

Every non-public route must use one of the dependencies defined here.
Admins (is_admin=True in JWT claims) bypass all permission checks.

Usage:
    @router.post("/tickets/")
    async def create_ticket(
        body: TicketCreate,
        current_user: dict = require_perm("ticket.create"),
    ):
        ...

Permission keys mirror osTicket's internal PERM_* constants but are expressed
as dot-namespaced strings.  The full mapping is in notes/OSTICKET_CAPABILITIES.md §8.1.4.

Defined constants (add here as new domains are implemented):

  ticket.create      ticket.reply       ticket.edit
  ticket.close       ticket.delete      ticket.transfer
  ticket.assign      ticket.merge       ticket.markanswered

  task.create        task.assign        task.close        task.delete

  user.create        user.edit          user.delete
  user.dir           user.manage

  org.create         org.edit           org.delete

  faq.manage         canned.manage      emails.banlist

  visibility.agents  visibility.departments
"""

from fastapi import Depends, HTTPException

from app.core.auth import get_current_user


def require_perm(perm: str):
    """Return a FastAPI dependency that enforces a single osTicket permission.

    Admin staff (is_admin=True) always pass regardless of the permission key.

    Args:
        perm: Dot-namespaced permission string, e.g. ``"ticket.reply"``.

    Returns:
        A ``Depends(...)`` object usable as a default value in route signatures.
    """

    async def _check(current_user: dict = Depends(get_current_user)) -> dict:
        if current_user.get("is_admin"):
            return current_user
        if perm not in current_user.get("permissions", []):
            raise HTTPException(
                status_code=403,
                detail=f"Permission required: {perm}",
            )
        return current_user

    return Depends(_check)


def require_admin():
    """Dependency: staff must have is_admin=True.

    Use for endpoints that have no finer-grained PERM_* equivalent in osTicket
    (e.g. staff/team management).
    """

    async def _check(current_user: dict = Depends(get_current_user)) -> dict:
        if not current_user.get("is_admin"):
            raise HTTPException(status_code=403, detail="Admin access required.")
        return current_user

    return Depends(_check)
