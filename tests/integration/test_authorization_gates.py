"""S2 — who may call what, through the real routes.

Each test names an agent by what osTicket has given them (global permissions, role permissions, the admin
flag) and checks the gate osTicket itself applies to that page. The agent's access is patched in where
``get_current_user`` would load it from the database.
"""

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.auth import create_access_token
from app.core.permissions import ALL_PERMISSIONS
from app.main import app
from tests.conftest import make_access

IDENTITY = {"staff_id": 5, "username": "agent", "name": "Agent", "email": "a@x.test"}   # identity only


def _headers():
    return {"Authorization": "Bearer " + create_access_token(IDENTITY, timedelta(minutes=5))}


def _as(access):
    """Make the API load ``access`` for whoever calls."""
    return patch("app.core.auth.load_access_context", new=AsyncMock(return_value=access))


async def _call(method, path, access, **kwargs):
    with _as(access):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            return await client.request(method, path, headers=_headers(), **kwargs)


# Everything a non-admin might hold: every permission osTicket has, but not the admin flag.
EVERYTHING_BUT_ADMIN = make_access(5, sorted(ALL_PERMISSIONS), is_admin=False)
ADMIN_WITH_NOTHING = make_access(5, [], is_admin=True)

ADMIN_PANEL_READS = [
    "/api/v1/staff", "/api/v1/staff/1", "/api/v1/departments", "/api/v1/departments/1",
    "/api/v1/teams", "/api/v1/teams/1", "/api/v1/topics", "/api/v1/topics/1", "/api/v1/slas",
    "/api/v1/slas/1", "/api/v1/priorities", "/api/v1/priorities/1", "/api/v1/forms", "/api/v1/forms/1",
    "/api/v1/banlist", "/api/v1/banlist/1", "/api/v1/commands", "/api/v1/commands/1",
]


# ── the Admin Panel is for administrators, whatever permissions they lack or hold ─────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ADMIN_PANEL_READS)
async def test_admin_panel_reads_refuse_a_non_admin_even_holding_every_permission(path):
    resp = await _call("GET", path, EVERYTHING_BUT_ADMIN)
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Admin access required."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method, path, body",
    [
        ("POST", "/api/v1/staff", {}), ("PATCH", "/api/v1/staff/1", {}), ("DELETE", "/api/v1/staff/1", None),
        ("POST", "/api/v1/departments", {}), ("POST", "/api/v1/teams", {}), ("POST", "/api/v1/topics", {}),
        ("POST", "/api/v1/slas", {}), ("POST", "/api/v1/priorities", {}), ("POST", "/api/v1/forms", {}),
        ("POST", "/api/v1/banlist", {}), ("DELETE", "/api/v1/banlist/1", None),
    ],
)
async def test_admin_panel_writes_refuse_a_non_admin(method, path, body):
    resp = await _call(method, path, EVERYTHING_BUT_ADMIN, **({"json": body} if body is not None else {}))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_an_admin_needs_no_extra_permission_for_the_admin_panel():
    with patch("app.modules.banlist.service.repo.list_banlist", new=AsyncMock(return_value=[])):
        resp = await _call("GET", "/api/v1/banlist", ADMIN_WITH_NOTHING)
    assert resp.status_code == 200
    with patch("app.modules.staff.service.repo.list_departments", new=AsyncMock(return_value=[])):
        resp = await _call("GET", "/api/v1/departments", ADMIN_WITH_NOTHING)
    assert resp.status_code == 200


# ── the user directory: user.dir, a *global* permission ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_user_directory_needs_user_dir_not_user_create():
    with patch("app.modules.users.service.repo.list_users", new=AsyncMock(return_value=[])):
        assert (await _call("GET", "/api/v1/users", make_access(5, ["user.create"]))).status_code == 403
        assert (await _call("GET", "/api/v1/users", make_access(5, ["user.dir"]))).status_code == 200


@pytest.mark.asyncio
async def test_user_dir_lets_you_look_but_not_touch():
    access = make_access(5, ["user.dir"])
    assert (await _call("POST", "/api/v1/users", access, json={})).status_code == 403
    assert (await _call("PATCH", "/api/v1/users/1", access, json={})).status_code == 403
    assert (await _call("DELETE", "/api/v1/users/1", access)).status_code == 403


@pytest.mark.asyncio
async def test_a_user_write_passes_the_gate_with_the_matching_permission():
    """An empty body then fails validation (422), which proves the permission gate was passed."""
    assert (await _call("POST", "/api/v1/users", make_access(5, ["user.create"]), json={})).status_code == 422
    assert (await _call("PATCH", "/api/v1/users/1", make_access(5, ["user.edit"]), json={"email": "x"})).status_code in (202, 422)


@pytest.mark.asyncio
async def test_organizations_follow_the_directory_and_their_own_write_permissions():
    with patch("app.modules.users.service.repo.list_organizations", new=AsyncMock(return_value=[])):
        assert (await _call("GET", "/api/v1/orgs", make_access(5, ["org.create"]))).status_code == 403
        assert (await _call("GET", "/api/v1/orgs", make_access(5, ["user.dir"]))).status_code == 200
    assert (await _call("POST", "/api/v1/orgs", make_access(5, ["user.dir"]), json={})).status_code == 403
    assert (await _call("POST", "/api/v1/orgs", make_access(5, ["org.create"]), json={})).status_code == 422


# ── knowledge base: anyone reads, faq.manage (global) writes; canned: canned.manage (role) ────────


@pytest.mark.asyncio
async def test_any_agent_may_read_the_knowledge_base():
    with patch("app.modules.kb.service.repo.list_faqs", new=AsyncMock(return_value=[])):
        assert (await _call("GET", "/api/v1/kb/faqs", make_access(5, []))).status_code == 200
    with patch("app.modules.kb.service.repo.list_categories", new=AsyncMock(return_value=[])):
        assert (await _call("GET", "/api/v1/kb/categories", make_access(5, []))).status_code == 200


@pytest.mark.asyncio
async def test_writing_the_knowledge_base_needs_faq_manage():
    for path in ("/api/v1/kb/faqs", "/api/v1/kb/categories"):
        assert (await _call("POST", path, make_access(5, []), json={})).status_code == 403
        assert (await _call("POST", path, make_access(5, ["canned.manage", "ticket.create"]), json={})).status_code == 403
        assert (await _call("POST", path, make_access(5, ["faq.manage"]), json={})).status_code == 422


@pytest.mark.asyncio
async def test_canned_responses_need_canned_manage_in_any_of_the_agents_roles():
    from app.core.access import AccessContext

    with patch("app.modules.kb.service.repo.list_canned", new=AsyncMock(return_value=[])):
        assert (await _call("GET", "/api/v1/canned", make_access(5, []))).status_code == 403
        assert (await _call("GET", "/api/v1/canned", make_access(5, ["canned.manage"]))).status_code == 200
        # granted only by the role held in an *extra* department: still "any of my roles" (hasPerm(.., false))
        extra_only = AccessContext(
            staff_id=5, dept_roles={1: frozenset({"ticket.create"}), 2: frozenset({"canned.manage"})},
            dept_ids=frozenset({1, 2}),
        )
        assert (await _call("GET", "/api/v1/canned", extra_only)).status_code == 200


# ── search ────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_any_agent_may_search_but_only_within_what_they_may_see():
    from app.core.scope import UNRESTRICTED

    search = AsyncMock(return_value=[])
    with patch("app.modules.search.service.repo.search_all", new=search):
        plain = make_access(5, [])
        assert (await _call("GET", "/api/v1/search?q=test", plain)).status_code == 200
        kwargs = search.await_args.kwargs
        assert kwargs["access"] is plain          # scoped to this agent
        assert kwargs["include_directory"] is False

        await _call("GET", "/api/v1/search?q=test", make_access(5, ["user.dir"]))
        assert search.await_args.kwargs["include_directory"] is True     # the directory needs user.dir

        await _call("GET", "/api/v1/search?q=test", make_access(5, ["search.all"]))
        assert search.await_args.kwargs["access"] is UNRESTRICTED        # search.all lifts ticket scoping


@pytest.mark.asyncio
async def test_being_an_admin_does_not_widen_search():
    """osTicket has no admin exemption for search: an admin without search.all is scoped like anyone."""
    search = AsyncMock(return_value=[])
    with patch("app.modules.search.service.repo.search_all", new=search):
        await _call("GET", "/api/v1/search?q=test", ADMIN_WITH_NOTHING)
    assert search.await_args.kwargs["access"] is not __import__("app.core.scope", fromlist=["x"]).UNRESTRICTED


# ── permissions are read on every request ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_permission_change_takes_effect_on_the_next_request_with_the_same_token():
    headers = _headers()
    states = [make_access(5, ["user.dir"]), make_access(5, []), make_access(5, ["user.dir"])]
    seen = []
    with patch("app.core.auth.load_access_context", new=AsyncMock(side_effect=states)):
        with patch("app.modules.users.service.repo.list_users", new=AsyncMock(return_value=[])):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
                for _ in states:
                    seen.append((await client.get("/api/v1/users", headers=headers)).status_code)
    assert seen == [200, 403, 200]          # revoked immediately, and restored immediately


@pytest.mark.asyncio
async def test_a_demoted_admin_loses_the_admin_panel_at_once():
    headers = _headers()
    states = [make_access(5, [], is_admin=True), make_access(5, [], is_admin=False)]
    seen = []
    with patch("app.core.auth.load_access_context", new=AsyncMock(side_effect=states)):
        with patch("app.modules.banlist.service.repo.list_banlist", new=AsyncMock(return_value=[])):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
                for _ in states:
                    seen.append((await client.get("/api/v1/banlist", headers=headers)).status_code)
    assert seen == [200, 403]


# ── /me ───────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_me_reports_the_agents_current_access():
    resp = await _call("GET", "/api/v1/auth/me", make_access(5, ["user.dir", "ticket.reply"], dept_ids=frozenset({1, 4})))
    body = resp.json()
    assert resp.status_code == 200
    assert body["global_permissions"] == ["user.dir"]
    assert body["roles"] == {"1": ["ticket.reply"]}
    assert body["department_ids"] == [1, 4]
    assert "permissions" not in body and "exp" not in body        # not a dump of the token
