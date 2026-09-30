"""osTicket's permission keys — every key the API may ask about, and where osTicket keeps it.

osTicket has two kinds of permission and the difference matters:

* **Global permissions** are stored on the staff member (``ost_staff.permissions``, a JSON map) and are
  checked with ``$staff->hasPerm($key)``. They govern the agent panel's directory and knowledge base:
  ``user.*``, ``org.*``, ``faq.manage``, ``emails.banlist``, ``visibility.*``, ``stats.agents``,
  ``search.all``.
* **Role permissions** are stored on the agent's *roles* (``ost_role.permissions``). An agent has a
  primary role for their primary department and may have a different role in each extra department
  (``ost_staff_dept_access``). ``$staff->hasPerm($key, false)`` asks "does any of my roles grant this";
  the exact per-department answer is ``$staff->getRole($dept)``. ``ticket.*``, ``task.*``,
  ``thread.edit`` and ``canned.manage`` are role permissions.

There is deliberately **no admin bypass** here: osTicket's ``hasPerm`` has none (an admin's stored
permissions are simply all switched on when the account is created). What administrators get for free
is the Admin Panel: those pages are gated by ``isAdmin()`` alone, which the API expresses as
``require_admin()``.

The keys below are the ``const PERM_*`` values of osTicket 1.18.1 (``include/class.*.php``).
``require_perm()`` refuses, at import time, a key that is not listed — a guard on a permission that
does not exist can only ever be satisfied by nobody, which is how ``kb.manage`` and friends went
unnoticed.
"""

GLOBAL_PERMISSIONS = frozenset({
    "user.create", "user.edit", "user.delete", "user.manage", "user.dir",
    "org.create", "org.edit", "org.delete",
    "faq.manage",
    "emails.banlist",
    "visibility.departments", "visibility.agents",
    "stats.agents",
    "search.all",
})

ROLE_PERMISSIONS = frozenset({
    "ticket.create", "ticket.edit", "ticket.reply", "ticket.assign", "ticket.transfer",
    "ticket.close", "ticket.delete", "ticket.merge", "ticket.link", "ticket.refer",
    "ticket.release", "ticket.markanswered",
    "task.create", "task.edit", "task.reply", "task.assign", "task.transfer",
    "task.close", "task.delete",
    "thread.edit",
    "canned.manage",
})

ALL_PERMISSIONS = GLOBAL_PERMISSIONS | ROLE_PERMISSIONS


def permission_kind(perm: str) -> str:
    """``"global"`` or ``"role"``; raises for a key osTicket does not have."""
    if perm in GLOBAL_PERMISSIONS:
        return "global"
    if perm in ROLE_PERMISSIONS:
        return "role"
    raise ValueError(
        f"{perm!r} is not an osTicket permission key. Real keys are listed in app/core/permissions.py; "
        "for an Admin Panel operation use require_admin() instead."
    )
