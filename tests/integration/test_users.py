"""Integration tests for users and organizations endpoints (Phase 4)."""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import select

from app.integration.osticket.commands import IntegrationInbox
from app.main import app
from tests.conftest import TestingSessionLocal

_NOW = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)

MOCK_ORG = {
    "id": 1,
    "name": "Acme Corp",
    "created": _NOW,
    "updated": _NOW,
    "address": "123 Coyote Way",
    "phone": "555-0100",
    "website": "www.acme.corp",
    "internal_notes": "Key account",
    "manager": "t4", "domain": "acme.corp", "status": 0x1 | 0x4 | 0x8,
}

MOCK_USER = {
    "id": 1,
    "name": "Wile E. Coyote",
    "org_id": 1,
    "org_name": "Acme Corp",
    "status": 0,
    "created": _NOW,
    "updated": _NOW,
    "email": "wile@acme.corp",
    "phone": "555-0101",
    "mobile": "555-0102",
    "internal_notes": "VIP",
}


def _token(permissions: list[str], is_admin: bool = False) -> str:
    from datetime import timedelta
    from app.core.auth import create_access_token
    return create_access_token(
        {"staff_id": 5, "username": "agent", "name": "Test Agent",
         "email": "agent@test.local", "is_admin": is_admin, "permissions": permissions},
        expires_delta=timedelta(minutes=5),
    )

def _auth(perms: list[str] | None = None, is_admin: bool = False) -> dict:
    return {"Authorization": f"Bearer {_token(perms or [], is_admin)}"}

async def _inbox_rows() -> list[IntegrationInbox]:
    async with TestingSessionLocal() as s:
        result = await s.execute(select(IntegrationInbox))
        return result.scalars().all()


# ── Organizations ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_orgs_returns_200_with_total_member_count_search_and_sort():
    listed = AsyncMock(return_value=[dict(MOCK_ORG, user_count=3)])
    with patch("app.modules.users.service.repo.list_organizations", new=listed), \
         patch("app.modules.users.service.repo.count_organizations", new=AsyncMock(return_value=7)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/orgs", params={"q": "acme", "sort": "users", "descending": "true"},
                                headers=_auth(["user.dir"]))
            bad = await ac.get("/api/v1/orgs", params={"sort": "nonsense"}, headers=_auth(["user.dir"]))
    assert resp.status_code == 200 and bad.status_code == 422
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["name"] == "Acme Corp" and data["items"][0]["user_count"] == 3
    assert data["total"] == 7
    assert (listed.await_args.kwargs["q"], listed.await_args.kwargs["sort"], listed.await_args.kwargs["descending"]) == (
        "acme", "users", True)


@pytest.mark.asyncio
async def test_get_org_returns_detail():
    with patch("app.modules.users.service.repo.get_organization", new=AsyncMock(return_value=dict(MOCK_ORG))), \
         patch("app.modules.users.service.repo.get_organization_primary_contacts", new=AsyncMock(return_value=[7])), \
         patch("app.modules.users.service.repo.get_organization_custom_fields", new=AsyncMock(return_value=[
             {"name": "tier", "label": "Tier", "type": "choices", "value": '{"g": "Gold"}', "value_id": 4},
             {"name": "website", "label": "Website", "type": "text", "value": "dup", "value_id": None}])):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/orgs/1", headers=_auth(["user.dir"]))
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Acme Corp"
    assert data["address"] == "123 Coyote Way"
    assert (data["manager_staff_id"], data["manager_team_id"], data["domain"]) == (0, 4, "acme.corp")
    assert (data["sharing"], data["collab_all_members"], data["collab_primary_contacts"],
            data["assign_account_manager"], data["primary_contact_ids"]) == ("primary_contacts", True, False, True, [7])
    assert data["custom_fields"] == [{"name": "tier", "label": "Tier", "type": "choices", "value": '{"g": "Gold"}', "value_id": 4}]


@pytest.mark.asyncio
async def test_org_settings_are_queued_and_validated():
    body = {"manager_staff_id": 3, "domain": "a.com,b.com", "sharing": "all_members", "collab_all_members": True,
            "assign_account_manager": False, "primary_contact_ids": [7, 8]}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        ok = await ac.patch("/api/v1/orgs/2", json=body, headers=_auth(["org.edit"]))
        both = await ac.patch("/api/v1/orgs/2", json={"manager_staff_id": 1, "manager_team_id": 2}, headers=_auth(["org.edit"]))
        mode = await ac.patch("/api/v1/orgs/2", json={"sharing": "everyone"}, headers=_auth(["org.edit"]))
        dup = await ac.patch("/api/v1/orgs/2", json={"primary_contact_ids": [7, 7]}, headers=_auth(["org.edit"]))
        denied = await ac.patch("/api/v1/orgs/2", json={"domain": "x.com"}, headers=_auth(["org.create"]))
    assert (ok.status_code, both.status_code, mode.status_code, dup.status_code, denied.status_code) == (
        202, 422, 422, 422, 403)
    assert json.loads((await _inbox_rows())[0].payload) == {"org_id": 2, **body}


@pytest.mark.asyncio
async def test_create_org_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/orgs",
            json={"name": "New Org", "website": "new.org"},
            headers=_auth(["org.create"]),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "org.create"
    payload = json.loads(rows[0].payload)
    assert payload["name"] == "New Org"


@pytest.mark.asyncio
async def test_update_org_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.patch(
            "/api/v1/orgs/2",
            json={"name": "Updated Org"},
            headers=_auth(["org.edit"]),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "org.update"
    assert rows[0].aggregate_id == "2"
    payload = json.loads(rows[0].payload)
    assert payload["name"] == "Updated Org"


@pytest.mark.asyncio
async def test_delete_org_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.delete("/api/v1/orgs/3", headers=_auth(["org.delete"]))
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "org.delete"
    assert rows[0].aggregate_id == "3"


# ── Users ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_users_returns_200_with_total_status_ticket_count_search_and_sort():
    rows = [dict(MOCK_USER, ticket_count=4, account_id=9, account_flags=0x1),
            dict(MOCK_USER, id=2, account_id=None, account_flags=None),
            dict(MOCK_USER, id=3, account_id=5, account_flags=0x1 | 0x2),
            dict(MOCK_USER, id=4, account_id=6, account_flags=0)]
    listed = AsyncMock(return_value=rows)
    with patch("app.modules.users.service.repo.list_users", new=listed), \
         patch("app.modules.users.service.repo.count_users", new=AsyncMock(return_value=40)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/users", params={"q": "coyote", "sort": "status", "org_id": 1},
                                headers=_auth(["user.dir"]))
            bad = await ac.get("/api/v1/users", params={"sort": "nonsense"}, headers=_auth(["user.dir"]))
    assert resp.status_code == 200 and bad.status_code == 422
    data = resp.json()
    assert data["items"][0]["name"] == "Wile E. Coyote" and data["total"] == 40
    assert [i["account_status"] for i in data["items"]] == ["active", "guest", "locked", "pending"]
    assert data["items"][0]["ticket_count"] == 4 and data["items"][1]["ticket_count"] == 0
    kwargs = listed.await_args.kwargs
    assert (kwargs["q"], kwargs["sort"], kwargs["org_id"], kwargs["descending"]) == ("coyote", "status", 1, False)


@pytest.mark.asyncio
async def test_get_user_returns_detail():
    with patch("app.modules.users.service.repo.get_user", new=AsyncMock(return_value=dict(MOCK_USER, status=1))), \
         patch("app.modules.users.service.repo.get_user_account", new=AsyncMock(return_value=None)), \
         patch("app.modules.users.service.repo.get_user_custom_fields", new=AsyncMock(return_value=[])):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/users/1", headers=_auth(["user.dir"]))
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Wile E. Coyote"
    assert data["email"] == "wile@acme.corp"
    assert data["account"] is None and data["is_primary_contact"] is True


@pytest.mark.asyncio
async def test_user_detail_decodes_the_account_status_and_never_a_password():
    acct = {"username": "wile", "timezone": "UTC", "status": 0x1 | 0x2 | 0x4, "registered": _NOW, "passwd": "x"}
    with patch("app.modules.users.service.repo.get_user", new=AsyncMock(return_value=dict(MOCK_USER))), \
         patch("app.modules.users.service.repo.get_user_account", new=AsyncMock(return_value=acct)), \
         patch("app.modules.users.service.repo.get_user_custom_fields", new=AsyncMock(return_value=[])):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            data = (await ac.get("/api/v1/users/1", headers=_auth(["user.dir"]))).json()
    assert data["account"]["status"] == "locked" and data["account"]["require_password_reset"] is True
    assert data["account"]["is_confirmed"] is True and data["account"]["forbid_password_reset"] is False
    assert "passwd" not in json.dumps(data) and "password" not in data["account"]


@pytest.mark.asyncio
async def test_account_commands_are_queued_validated_and_need_user_manage():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        reg = await ac.post("/api/v1/users/4/account", json={"password": "Tmp-pass-1!", "require_password_reset": True},
                            headers=_auth(["user.manage"]))
        mail = await ac.post("/api/v1/users/4/account", json={"send_email": True}, headers=_auth(["user.manage"]))
        upd = await ac.patch("/api/v1/users/4/account", json={"is_locked": True}, headers=_auth(["user.manage"]))
        reset = await ac.post("/api/v1/users/4/account/password-reset", headers=_auth(["user.manage"]))
        conf = await ac.post("/api/v1/users/4/account/confirmation", headers=_auth(["user.manage"]))
        no_pw = await ac.post("/api/v1/users/4/account", json={}, headers=_auth(["user.manage"]))
        email_name = await ac.patch("/api/v1/users/4/account", json={"username": "a@b.com"}, headers=_auth(["user.manage"]))
        empty = await ac.patch("/api/v1/users/4/account", json={}, headers=_auth(["user.manage"]))
        denied = await ac.patch("/api/v1/users/4/account", json={"is_locked": True}, headers=_auth(["user.edit"]))
    assert [r.status_code for r in (reg, mail, upd, reset, conf)] == [202] * 5
    assert (no_pw.status_code, email_name.status_code, empty.status_code, denied.status_code) == (422, 422, 422, 403)
    rows = await _inbox_rows()
    assert [r.command_type for r in rows] == ["user.account.register", "user.account.register", "user.account.update",
                                              "user.account.password_reset", "user.account.confirmation"]
    assert json.loads(rows[2].payload) == {"user_id": 4, "is_locked": True}


@pytest.mark.asyncio
async def test_create_user_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/users",
            json={"name": "New User", "email": "new@user.com"},
            headers=_auth(["user.create"]),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "user.create"
    payload = json.loads(rows[0].payload)
    assert payload["name"] == "New User"


@pytest.mark.asyncio
async def test_update_user_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.patch(
            "/api/v1/users/4",
            json={"name": "Updated User"},
            headers=_auth(["user.edit"]),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "user.update"
    assert rows[0].aggregate_id == "4"


@pytest.mark.asyncio
async def test_delete_user_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.delete("/api/v1/users/5", headers=_auth(["user.delete"]))
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "user.delete"
    assert rows[0].aggregate_id == "5"


# ── Common checks ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_all_endpoints_require_auth():
    endpoints = [
        ("GET",    "/api/v1/orgs"),
        ("GET",    "/api/v1/orgs/1"),
        ("POST",   "/api/v1/orgs"),
        ("PATCH",  "/api/v1/orgs/1"),
        ("DELETE", "/api/v1/orgs/1"),
        ("GET",    "/api/v1/users"),
        ("GET",    "/api/v1/users/1"),
        ("POST",   "/api/v1/users"),
        ("PATCH",  "/api/v1/users/1"),
        ("DELETE", "/api/v1/users/1"),
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        for method, path in endpoints:
            func = getattr(ac, method.lower())
            kwargs = {} if method in ("GET", "DELETE") else {"json": {}}
            resp = await func(path, **kwargs)
            assert resp.status_code == 401, f"{method} {path} → {resp.status_code}"
