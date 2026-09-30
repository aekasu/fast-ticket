"""AccessContext — a faithful model of osTicket's Staff access rules (include/class.staff.py's PHP twin)."""

import pytest

from app.core import access as access_module
from app.core.access import FALLBACK_ROLE_PERMS, _perm_set, build_access_context, load_access_context

SEEDED_ADMIN_PERMS = (
    '{"user.create":1,"user.delete":1,"user.edit":1,"user.manage":1,"user.dir":1,"org.create":1,'
    '"org.delete":1,"org.edit":1,"faq.manage":1,"visibility.agents":1,"emails.banlist":1,'
    '"visibility.departments":1}'
)
ALL_ACCESS = '{"ticket.create":1,"ticket.reply":1,"ticket.close":1,"canned.manage":1,"ticket.delete":0}'
LIMITED = '{"ticket.create":1,"ticket.assign":1}'


def _rows(**over):
    rows = {
        "staff": {
            "staff_id": 5, "username": "agent", "firstname": "Ann", "lastname": "Agent", "email": "a@x.test",
            "isactive": 1, "isadmin": 0, "dept_id": 1, "role_id": 3, "assigned_only": 0, "permissions": None,
        },
        "dept_access": [],
        "roles": {3: LIMITED},
        "team_ids": [],
        "dept_ids": [1],
    }
    rows.update(over)
    return rows


def test_identity_is_carried_over():
    ctx = build_access_context(_rows())
    assert (ctx.staff_id, ctx.username, ctx.name, ctx.email) == (5, "agent", "Ann Agent", "a@x.test")
    assert ctx.is_active and not ctx.is_admin and not ctx.assigned_only


def test_null_staff_permissions_mean_no_global_permissions():
    """An agent created without any global permission (like a 'Limited Access' agent) has none."""
    ctx = build_access_context(_rows())
    assert ctx.global_perms == frozenset()
    assert not ctx.has_global("user.dir")


def test_global_permissions_come_from_the_staff_row():
    rows = _rows()
    rows["staff"]["permissions"] = SEEDED_ADMIN_PERMS
    ctx = build_access_context(rows)
    assert ctx.has_global("user.dir") and ctx.has_global("faq.manage") and ctx.has_global("visibility.agents")
    assert not ctx.has_global("search.all")


def test_role_permissions_come_from_the_role_not_the_staff_row():
    ctx = build_access_context(_rows())
    assert ctx.has_role_perm("ticket.assign")
    assert not ctx.has_global("ticket.assign")           # a role perm is not a global one
    assert not ctx.has_role_perm("user.dir")


def test_being_an_admin_grants_no_permission_by_itself():
    """osTicket's hasPerm() has no admin bypass: an admin's power is the Admin Panel, not every key."""
    rows = _rows()
    rows["staff"]["isadmin"] = 1
    ctx = build_access_context(rows)
    assert ctx.is_admin
    assert not ctx.has_global("user.dir")
    assert not ctx.has_role_perm("ticket.delete")


def test_permissions_switched_off_are_not_granted():
    """{"ticket.delete": 0} is stored by osTicket for an unticked box."""
    ctx = build_access_context(_rows(roles={3: ALL_ACCESS}))
    assert ctx.has_role_perm("ticket.reply")
    assert not ctx.has_role_perm("ticket.delete")


def test_primary_and_extra_department_roles_are_kept_apart():
    rows = _rows(
        dept_access=[{"dept_id": 2, "role_id": 1}, {"dept_id": 3, "role_id": 3}],
        roles={3: LIMITED, 1: ALL_ACCESS},
        dept_ids=[1, 2, 3],
    )
    ctx = build_access_context(rows)
    assert ctx.role_perms_for(1) == frozenset({"ticket.create", "ticket.assign"})
    assert "ticket.close" in ctx.role_perms_for(2)
    assert "ticket.close" not in ctx.role_perms_for(3)


def test_has_role_perm_asks_whether_any_role_grants_it():
    """Staff::hasPerm($perm, false): true if the primary role or any extra-department role has it."""
    rows = _rows(dept_access=[{"dept_id": 2, "role_id": 1}], roles={3: LIMITED, 1: ALL_ACCESS}, dept_ids=[1, 2])
    ctx = build_access_context(rows)
    assert ctx.has_role_perm("ticket.close")                    # only the Sales role has it
    assert not ctx.can_role_perm_in("ticket.close", 1)          # ...but not in the primary department


def test_a_department_without_a_role_gets_osticket_create_only_fallback():
    ctx = build_access_context(_rows())
    assert ctx.role_perms_for(99) == FALLBACK_ROLE_PERMS == frozenset({"ticket.create"})
    assert ctx.can_role_perm_in("ticket.create", 99)
    assert not ctx.can_role_perm_in("ticket.reply", 99)


def test_an_extra_row_for_the_primary_department_replaces_the_primary_role():
    rows = _rows(dept_access=[{"dept_id": 1, "role_id": 1}], roles={3: LIMITED, 1: ALL_ACCESS})
    assert "ticket.close" in build_access_context(rows).role_perms_for(1)


def test_a_missing_role_row_is_an_empty_role_not_an_error():
    ctx = build_access_context(_rows(roles={}))
    assert ctx.role_perms_for(1) == frozenset()


def test_department_access_is_the_visible_set_and_limited_agents_lose_it():
    ctx = build_access_context(_rows(dept_ids=[1, 4]))
    assert ctx.can_access_dept(1) and ctx.can_access_dept(4) and not ctx.can_access_dept(2)

    rows = _rows(dept_ids=[1, 4])
    rows["staff"]["assigned_only"] = 1
    limited = build_access_context(rows)
    assert limited.assigned_only
    assert not limited.can_access_dept(1)          # Staff::canAccessDept: !isAccessLimited() && ...


def test_team_membership():
    ctx = build_access_context(_rows(team_ids=[2, 7]))
    assert ctx.is_team_member(2) and ctx.is_team_member(7)
    assert not ctx.is_team_member(3) and not ctx.is_team_member(None) and not ctx.is_team_member(0)


def test_all_permissions_is_the_union_across_roles_and_global():
    rows = _rows(dept_access=[{"dept_id": 2, "role_id": 1}], roles={3: LIMITED, 1: ALL_ACCESS}, dept_ids=[1, 2])
    rows["staff"]["permissions"] = '{"user.dir":1}'
    perms = build_access_context(rows).all_permissions()
    assert perms == sorted(set(perms))
    assert {"user.dir", "ticket.assign", "ticket.close", "canned.manage"} <= set(perms)


def test_summary_is_json_friendly_and_shows_roles_per_department():
    rows = _rows(dept_access=[{"dept_id": 2, "role_id": 1}], roles={3: LIMITED, 1: ALL_ACCESS},
                 dept_ids=[2, 1], team_ids=[9])
    summary = build_access_context(rows).summary()
    assert summary["department_ids"] == [1, 2] and summary["team_ids"] == [9]
    assert summary["roles"]["1"] == ["ticket.assign", "ticket.create"]
    assert "ticket.close" in summary["roles"]["2"]
    assert summary["is_admin"] is False and summary["assigned_only"] is False


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, set()),
        ("", set()),
        ("not json", set()),
        ("[1, 2]", set()),
        ('{"a": 1, "b": 0, "c": true, "d": null}', {"a", "c"}),
        ({"a": 1, "b": 0}, {"a"}),
        (b'{"a": 1}', {"a"}),
    ],
)
def test_stored_permission_maps_are_read_tolerantly(raw, expected):
    assert _perm_set(raw) == frozenset(expected)


@pytest.mark.asyncio
async def test_load_returns_none_for_an_unknown_staff_member(monkeypatch):
    async def none(session, staff_id):
        return None

    monkeypatch.setattr(access_module.repo, "get_staff_access_rows", none)
    assert await load_access_context(None, 404) is None


@pytest.mark.asyncio
async def test_load_builds_a_context_from_the_repository_rows(monkeypatch):
    async def rows(session, staff_id):
        return _rows()

    monkeypatch.setattr(access_module.repo, "get_staff_access_rows", rows)
    ctx = await load_access_context(None, 5)
    assert ctx.staff_id == 5 and ctx.has_role_perm("ticket.assign")
