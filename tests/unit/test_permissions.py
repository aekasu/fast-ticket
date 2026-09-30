"""The permission registry is osTicket's, not ours."""

import re
from pathlib import Path

import pytest

from app.core.permissions import ALL_PERMISSIONS, GLOBAL_PERMISSIONS, ROLE_PERMISSIONS, permission_kind
from app.core.security import require_perm

# Every `const PERM_* = '...'` in osTicket 1.18.1's include/class.*.php (verified against the source:
#   grep -hn "const PERM_[A-Z_]* *= *" include/class.*.php)
OSTICKET_1_18_1_PERMISSIONS = {
    "stats.agents", "search.all", "emails.banlist", "visibility.departments", "visibility.agents",
    "user.create", "user.edit", "user.delete", "user.manage", "user.dir",
    "org.create", "org.edit", "org.delete",
    "faq.manage", "canned.manage",
    "ticket.create", "ticket.edit", "ticket.reply", "ticket.assign", "ticket.transfer", "ticket.close",
    "ticket.delete", "ticket.merge", "ticket.link", "ticket.refer", "ticket.release", "ticket.markanswered",
    "task.create", "task.edit", "task.reply", "task.assign", "task.transfer", "task.close", "task.delete",
    "thread.edit",
}


def test_the_registry_is_exactly_osticket_1_18_1s_permission_keys():
    assert ALL_PERMISSIONS == OSTICKET_1_18_1_PERMISSIONS


def test_a_permission_is_global_or_role_never_both():
    assert not (GLOBAL_PERMISSIONS & ROLE_PERMISSIONS)


@pytest.mark.parametrize("perm", ["user.dir", "org.edit", "faq.manage", "emails.banlist", "search.all", "visibility.agents"])
def test_directory_and_kb_permissions_are_global(perm):
    assert permission_kind(perm) == "global"


@pytest.mark.parametrize("perm", ["ticket.create", "task.close", "thread.edit", "canned.manage"])
def test_ticket_task_and_canned_permissions_are_role_permissions(perm):
    assert permission_kind(perm) == "role"


@pytest.mark.parametrize("bad", ["kb.manage", "banlist.manage", "staff.create", "dept.edit", "commands.read", "ticket.note", ""])
def test_a_key_osticket_does_not_have_is_refused_when_the_guard_is_built(bad):
    with pytest.raises(ValueError, match="not an osTicket permission key"):
        permission_kind(bad)
    with pytest.raises(ValueError):
        require_perm(bad)


def test_every_key_a_router_guards_with_is_real():
    """The guard fails at import anyway; this states the rule where a reader will look for it."""
    used = set()
    for router in Path("app/modules").glob("*/router.py"):
        used |= set(re.findall(r'require_perm\("([^"]+)"', router.read_text()))
    assert used, "found no guards — did the pattern stop matching?"
    assert used <= ALL_PERMISSIONS, f"not osTicket permissions: {sorted(used - ALL_PERMISSIONS)}"


def test_no_route_is_left_without_a_guard():
    """Every route except the public ones must declare require_perm / require_admin / require_staff."""
    from app.main import app

    public = {"/health", "/api/v1/auth/login", "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc", "/api/v1/ws"}
    guards = ("_check",)   # the inner dependency function every require_* returns
    unguarded = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if path in public or not hasattr(route, "dependant"):
            continue
        names = {d.call.__name__ for d in route.dependant.dependencies if getattr(d, "call", None)}
        # /me and the like use get_current_user directly
        if not names & set(guards) and "get_current_user" not in names:
            unguarded.append(path)
    assert not unguarded, f"routes with no authentication/permission dependency: {unguarded}"
