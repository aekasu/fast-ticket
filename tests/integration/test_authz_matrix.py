"""The authorization matrix: every route, every kind of agent, one explicit policy per route.

``POLICY`` is the reviewed, written-down answer to "who may call this?" for all routes. The test evaluates the
gate each route *actually carries* (the ``require_*`` dependency) for a set of agents — one with nothing, one
holding each single osTicket permission, one holding every permission, and administrators with and without
permissions — and checks that exactly the agents the policy names get through.

Changing who may call a route therefore means editing this table on purpose. A route missing from it fails the
suite, so a new endpoint cannot ship without someone deciding its audience; a stale entry fails too.

Policies:  ``public`` (no authentication), ``authenticated`` (any valid login, no further gate),
           ``staff`` (any enabled agent; what they *see* is narrowed in the query),
           ``admin`` (osTicket's isAdmin()), ``perm:<osTicket key>`` (no admin bypass).
"""

import pytest
from fastapi import HTTPException

from app.core.permissions import ALL_PERMISSIONS
from app.main import app
from tests.conftest import make_access
from tests.unit.app_routes import all_routes, guards, uses_current_user

POLICY = {
    "POST /api/v1/auth/login": "public",
    "GET /health": "public",
    "GET /api/v1/auth/me": "authenticated",

    # ── Admin Panel objects ────────────────────────────────────────────────
    **{f"{m} {p}": "admin" for m, p in [
        ("GET", "/api/v1/roles"), ("GET", "/api/v1/banlist"), ("POST", "/api/v1/banlist"), ("GET", "/api/v1/banlist/{ban_id}"),
        ("PATCH", "/api/v1/banlist/{ban_id}"), ("DELETE", "/api/v1/banlist/{ban_id}"),
        ("POST", "/api/v1/departments"), ("GET", "/api/v1/departments/{dept_id}"),
        ("PATCH", "/api/v1/departments/{dept_id}"), ("DELETE", "/api/v1/departments/{dept_id}"),
        ("GET", "/api/v1/forms"), ("POST", "/api/v1/forms"), ("GET", "/api/v1/forms/{form_id}"),
        ("PATCH", "/api/v1/forms/{form_id}"), ("DELETE", "/api/v1/forms/{form_id}"),
        ("POST", "/api/v1/forms/{form_id}/fields"), ("PATCH", "/api/v1/forms/{form_id}/fields/{field_id}"),
        ("DELETE", "/api/v1/forms/{form_id}/fields/{field_id}"),
        ("POST", "/api/v1/priorities"), ("GET", "/api/v1/email-templates"), ("POST", "/api/v1/email-templates"), ("GET", "/api/v1/email-templates/{set_id}"), ("PATCH", "/api/v1/email-templates/{set_id}"), ("DELETE", "/api/v1/email-templates/{set_id}"), ("GET", "/api/v1/email-templates/{set_id}/messages/{code_name}"), ("PUT", "/api/v1/email-templates/{set_id}/messages/{code_name}"), ("GET", "/api/v1/settings/system"), ("PATCH", "/api/v1/settings/system"), ("GET", "/api/v1/settings/tickets"), ("PATCH", "/api/v1/settings/tickets"), ("GET", "/api/v1/settings/tasks"), ("PATCH", "/api/v1/settings/tasks"), ("GET", "/api/v1/settings/agents"), ("PATCH", "/api/v1/settings/agents"), ("GET", "/api/v1/settings/users"), ("PATCH", "/api/v1/settings/users"), ("GET", "/api/v1/settings/kb"), ("PATCH", "/api/v1/settings/kb"), ("GET", "/api/v1/settings/emails"), ("PATCH", "/api/v1/settings/emails"), ("GET", "/api/v1/settings/pages"), ("PATCH", "/api/v1/settings/pages"), ("GET", "/api/v1/lists"), ("POST", "/api/v1/lists"), ("GET", "/api/v1/lists/{list_id}"), ("PATCH", "/api/v1/lists/{list_id}"), ("DELETE", "/api/v1/lists/{list_id}"), ("GET", "/api/v1/lists/{list_id}/items"), ("POST", "/api/v1/lists/{list_id}/items"), ("PATCH", "/api/v1/lists/{list_id}/items/{item_id}"), ("DELETE", "/api/v1/lists/{list_id}/items/{item_id}"), ("GET", "/api/v1/pages"), ("POST", "/api/v1/pages"), ("GET", "/api/v1/pages/{page_id}"), ("PATCH", "/api/v1/pages/{page_id}"), ("DELETE", "/api/v1/pages/{page_id}"), ("GET", "/api/v1/api-keys"), ("POST", "/api/v1/api-keys"), ("GET", "/api/v1/api-keys/{key_id}"), ("PATCH", "/api/v1/api-keys/{key_id}"), ("DELETE", "/api/v1/api-keys/{key_id}"), ("GET", "/api/v1/logs"), ("GET", "/api/v1/logs/{log_id}"), ("POST", "/api/v1/logs/delete"), ("GET", "/api/v1/system/info"), ("POST", "/api/v1/roles"), ("GET", "/api/v1/roles/{role_id}"), ("PATCH", "/api/v1/roles/{role_id}"), ("DELETE", "/api/v1/roles/{role_id}"), ("GET", "/api/v1/priorities/{priority_id}"),
        ("PATCH", "/api/v1/priorities/{priority_id}"), ("DELETE", "/api/v1/priorities/{priority_id}"),
        ("GET", "/api/v1/slas"), ("POST", "/api/v1/slas"), ("GET", "/api/v1/slas/{sla_id}"),
        ("PATCH", "/api/v1/slas/{sla_id}"), ("DELETE", "/api/v1/slas/{sla_id}"),
        ("GET", "/api/v1/staff"), ("POST", "/api/v1/staff"), ("GET", "/api/v1/staff/{staff_id}"),
        ("PATCH", "/api/v1/staff/{staff_id}"), ("DELETE", "/api/v1/staff/{staff_id}"),
        ("GET", "/api/v1/teams"), ("POST", "/api/v1/teams"), ("GET", "/api/v1/teams/{team_id}"),
        ("PATCH", "/api/v1/teams/{team_id}"), ("DELETE", "/api/v1/teams/{team_id}"),
        ("POST", "/api/v1/topics"), ("GET", "/api/v1/topics/{topic_id}"),
        ("PATCH", "/api/v1/topics/{topic_id}"), ("DELETE", "/api/v1/topics/{topic_id}"),
    ]},

    # ── reads any agent may make; the query narrows what they get back ─────
    **{f"GET {p}": "staff" for p in [
        "/api/v1/agents", "/api/v1/tickets/queues", "/api/v1/canned", "/api/v1/canned/{canned_id}", "/api/v1/commands", "/api/v1/commands/{command_id}",
        "/api/v1/departments", "/api/v1/kb/categories", "/api/v1/kb/categories/{category_id}",
        "/api/v1/kb/faqs", "/api/v1/kb/faqs/{faq_id}", "/api/v1/priorities", "/api/v1/profile", "/api/v1/dashboard/report", "/api/v1/ticket-statuses", "/api/v1/permissions", "/api/v1/forms/for/{kind}", "/api/v1/settings/attachments", "/api/v1/search",
        "/api/v1/tasks/", "/api/v1/tasks/queues", "/api/v1/tasks/{task_id}", "/api/v1/tasks/{task_id}/collaborators", "/api/v1/tickets/", "/api/v1/tickets/{ticket_id}", "/api/v1/tickets/{ticket_id}/collaborators", "/api/v1/tickets/{ticket_id}/referrals", "/api/v1/tickets/{ticket_id}/related", "/api/v1/tickets/{ticket_id}/canned/{canned_id}",
        "/api/v1/tickets/{ticket_id}/attachments/{attachment_id}", "/api/v1/tasks/{task_id}/attachments/{attachment_id}",
        "/api/v1/kb/faqs/{faq_id}/attachments/{attachment_id}", "/api/v1/canned/{canned_id}/attachments/{attachment_id}",
        "/api/v1/topics",
    ]},

    # ── knowledge base and canned responses ────────────────────────────────
    "POST /api/v1/kb/categories": "perm:faq.manage", "PATCH /api/v1/kb/categories/{category_id}": "perm:faq.manage",
    "DELETE /api/v1/kb/categories/{category_id}": "perm:faq.manage",
    "POST /api/v1/kb/faqs": "perm:faq.manage", "PATCH /api/v1/kb/faqs/{faq_id}": "perm:faq.manage",
    "DELETE /api/v1/kb/faqs/{faq_id}": "perm:faq.manage",
    "POST /api/v1/canned": "perm:canned.manage", "PATCH /api/v1/canned/{canned_id}": "perm:canned.manage",
    "DELETE /api/v1/canned/{canned_id}": "perm:canned.manage",

    # ── directory ──────────────────────────────────────────────────────────
    "GET /api/v1/users": "perm:user.dir", "GET /api/v1/users/{user_id}": "perm:user.dir",
    "POST /api/v1/users": "perm:user.create", "PATCH /api/v1/users/{user_id}": "perm:user.edit",
    "DELETE /api/v1/users/{user_id}": "perm:user.delete",
    "GET /api/v1/orgs": "perm:user.dir", "GET /api/v1/orgs/{org_id}": "perm:user.dir",
    "POST /api/v1/orgs": "perm:org.create", "PATCH /api/v1/orgs/{org_id}": "perm:org.edit",
    "DELETE /api/v1/orgs/{org_id}": "perm:org.delete",

    # ── tickets ────────────────────────────────────────────────────────────
    "POST /api/v1/tickets/": "perm:ticket.create", "DELETE /api/v1/tickets/{ticket_id}": "perm:ticket.delete",
    "PATCH /api/v1/tickets/{ticket_id}": "perm:ticket.edit",
    "POST /api/v1/users/{user_id}/account": "perm:user.manage",
    "PATCH /api/v1/users/{user_id}/account": "perm:user.manage",
    "POST /api/v1/users/{user_id}/account/password-reset": "perm:user.manage",
    "POST /api/v1/users/{user_id}/account/confirmation": "perm:user.manage",
    "POST /api/v1/tickets/{ticket_id}/answered": "perm:ticket.markanswered",
    "POST /api/v1/tickets/{ticket_id}/unanswered": "perm:ticket.markanswered",
    "GET /api/v1/tickets/{ticket_id}/assignees": "perm:ticket.assign",
    "POST /api/v1/tickets/{ticket_id}/assign": "perm:ticket.assign",
    "POST /api/v1/tickets/{ticket_id}/assign-team": "perm:ticket.assign",
    "POST /api/v1/tickets/{ticket_id}/claim": "perm:ticket.assign",
    "POST /api/v1/tickets/{ticket_id}/refer": "perm:ticket.assign",
    "DELETE /api/v1/tickets/{ticket_id}/referrals/{referral_id}": "perm:ticket.assign",
    "POST /api/v1/tickets/{ticket_id}/release": "perm:ticket.release",
    "POST /api/v1/tickets/{ticket_id}/change-owner": "perm:ticket.edit",
    "POST /api/v1/tickets/{ticket_id}/close": "perm:ticket.close",
    "POST /api/v1/tickets/{ticket_id}/reopen": "perm:ticket.close",
    "PATCH /api/v1/tickets/{ticket_id}/thread/{entry_id}": "perm:ticket.reply",
    "POST /api/v1/tickets/{ticket_id}/thread/{entry_id}/resend": "perm:ticket.reply",
    "POST /api/v1/tickets/{ticket_id}/collaborators": "perm:ticket.edit",
    "PATCH /api/v1/tickets/{ticket_id}/collaborators/{user_id}": "perm:ticket.edit",
    "DELETE /api/v1/tickets/{ticket_id}/collaborators/{user_id}": "perm:ticket.edit",
    "POST /api/v1/tickets/{ticket_id}/note": "perm:ticket.reply",
    "POST /api/v1/tickets/{ticket_id}/reply": "perm:ticket.reply",
    "PATCH /api/v1/profile": "self",            # self-service write: any agent, their own record only
    "POST /api/v1/profile/password": "self",
    "POST /api/v1/tickets/{ticket_id}/merge": "perm:ticket.merge",
    "POST /api/v1/tickets/{ticket_id}/unmerge": "perm:ticket.merge",
    "POST /api/v1/tickets/{ticket_id}/link": "perm:ticket.link",
    "POST /api/v1/tickets/{ticket_id}/unlink": "perm:ticket.link",
    "POST /api/v1/tickets/{ticket_id}/transfer": "perm:ticket.transfer",

    # ── tasks ──────────────────────────────────────────────────────────────
    "GET /api/v1/tasks/{task_id}/assignees": "perm:task.assign",
    "POST /api/v1/tasks/": "perm:task.create", "DELETE /api/v1/tasks/{task_id}": "perm:task.delete",
    "PATCH /api/v1/tasks/{task_id}": "perm:task.edit", "POST /api/v1/tasks/{task_id}/assign": "perm:task.assign",
    "POST /api/v1/tasks/{task_id}/close": "perm:task.close", "POST /api/v1/tasks/{task_id}/reopen": "perm:task.close",
    "POST /api/v1/tasks/{task_id}/collaborators": "perm:ticket.edit",
    "POST /api/v1/users/import": "perm:user.create",
    "POST /api/v1/tasks/{task_id}/note": "perm:task.reply", "POST /api/v1/tasks/{task_id}/reply": "perm:task.reply",
    "PATCH /api/v1/tasks/{task_id}/thread/{entry_id}": "perm:task.reply", "POST /api/v1/tasks/{task_id}/thread/{entry_id}/resend": "perm:task.reply",
    "POST /api/v1/tasks/{task_id}/claim": "perm:task.assign", "POST /api/v1/tasks/{task_id}/transfer": "perm:task.transfer",
}


def _personas():
    """name -> AccessContext: nothing; each single permission; everything; and administrators."""
    personas = {
        "nothing": make_access(1, []),
        "admin, no permissions": make_access(1, [], is_admin=True),
        "every permission, not admin": make_access(1, sorted(ALL_PERMISSIONS)),
        "admin, every permission": make_access(1, sorted(ALL_PERMISSIONS), is_admin=True),
    }
    for key in sorted(ALL_PERMISSIONS):
        personas[f"only {key}"] = make_access(1, [key])
    return personas


def _expected(policy: str, name: str, access) -> bool:
    if policy in ("staff", "self"):
        return True
    if policy == "admin":
        return access.is_admin
    kind, _, key = policy.partition(":")
    assert kind == "perm", policy
    return key in access.global_perms or access.has_role_perm(key)      # no admin bypass


ROUTES = all_routes(app)


def test_the_matrix_saw_the_real_routes():
    """A previous version of a similar check iterated a lazy wrapper and looked at nothing."""
    assert len(ROUTES) >= 90
    assert {r.key for r in ROUTES} >= {"GET /api/v1/tickets/", "POST /api/v1/staff", "GET /health"}


def test_every_route_has_a_policy_and_every_policy_a_route():
    actual = {r.key for r in ROUTES}
    assert actual - set(POLICY) == set(), "routes with no policy — decide who may call them and add them to POLICY"
    assert set(POLICY) - actual == set(), "stale policy entries for routes that no longer exist"


def test_a_write_is_never_open_to_every_agent():
    open_writes = [k for k, v in POLICY.items() if v in ("staff", "public", "authenticated") and not k.startswith("GET ")
                   and k != "POST /api/v1/auth/login"]
    assert open_writes == []


def test_the_only_self_service_writes_are_the_profile_ones():
    assert {k for k, v in POLICY.items() if v == "self"} == {"PATCH /api/v1/profile", "POST /api/v1/profile/password"}


def test_only_reads_are_open_to_every_agent():
    assert [k for k, v in POLICY.items() if v == "staff" and not k.startswith("GET ")] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES, ids=lambda r: r.key)
async def test_each_route_lets_in_exactly_the_agents_its_policy_names(route):
    policy = POLICY[route.key]
    route_guards = guards(route)

    if policy == "public":
        assert route_guards == [] and not uses_current_user(route)
        return
    if policy == "authenticated":
        assert route_guards == [] and uses_current_user(route)         # a valid login, and nothing more
        return

    assert len(route_guards) == 1, f"{route.key}: expected exactly one require_* guard, found {len(route_guards)}"
    (gate,) = route_guards
    wrong = []
    for name, access in _personas().items():
        try:
            await gate(current_user={"access": access})
            passed = True
        except HTTPException as refused:
            assert refused.status_code == 403
            passed = False
        if passed != _expected(policy, name, access):
            wrong.append((name, "let in" if passed else "refused"))
    assert not wrong, f"{route.key} (policy {policy}) misbehaves for: {wrong}"
