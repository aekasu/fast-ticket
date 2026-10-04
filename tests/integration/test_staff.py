"""Integration tests for staff, teams, and departments endpoints (Phase 5)."""

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

MOCK_DEPT = {
    "id": 1,
    "name": "Support",
    "is_public": True,
    "created": _NOW,
    "updated": _NOW,
    "manager_id": 5,
    "email_id": 1,
    "flags": 0x4 | 0x2 | 0x10,   # active, no auto-claim, primary-members assignment
    "parent_id": None, "sla_id": 2, "schedule_id": 0, "template_id": 0, "autoresponse_email_id": 0,
    "signature": "sig", "group_membership": 1, "ticket_auto_response": 1, "message_auto_response": 0,
}

MOCK_TEAM = {
    "id": 1,
    "name": "Level 1 Support",
    "is_enabled": True,
    "created": _NOW,
    "updated": _NOW,
    "lead_id": 5,
    "notes": "First responders",
    "no_alerts": True,
}

MOCK_STAFF = {
    "id": 5,
    "username": "agent",
    "firstname": "Test",
    "lastname": "Agent",
    "email": "agent@test.local",
    "is_active": True,
    "is_admin": False,
    "dept_id": 1,
    "role_id": 1,
    "created": _NOW,
    "updated": _NOW,
    "phone": "555-0199",
    "mobile": "555-0198",
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


# ── Departments ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_departments_returns_200():
    with patch("app.modules.staff.service.repo.list_departments", new=AsyncMock(return_value=[MOCK_DEPT])):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/departments", headers=_auth(is_admin=True))
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["name"] == "Support"


@pytest.mark.asyncio
async def test_get_department_returns_detail():
    with patch("app.modules.staff.service.repo.get_department", new=AsyncMock(return_value=dict(MOCK_DEPT))), \
         patch("app.modules.staff.service.repo.get_department_members",
               new=AsyncMock(return_value=[{"staff_id": 4, "role_id": 2, "alerts": 0}])):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/departments/1", headers=_auth(is_admin=True))
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Support"
    assert data["manager_id"] == 5


@pytest.mark.asyncio
async def test_department_detail_decodes_the_flag_bits():
    with patch("app.modules.staff.service.repo.get_department", new=AsyncMock(return_value=dict(MOCK_DEPT))), \
         patch("app.modules.staff.service.repo.get_department_members",
               new=AsyncMock(return_value=[{"staff_id": 4, "role_id": 2, "alerts": 0}])):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            data = (await ac.get("/api/v1/departments/1", headers=_auth(is_admin=True))).json()
    assert (data["status"], data["assignment"], data["disable_auto_claim"], data["disable_reopen_auto_assign"]) == (
        "active", "primary", True, False)
    assert (data["alerts_to"], data["sla_id"], data["signature"]) == ("dept_and_extended", 2, "sig")
    assert data["ticket_auto_response"] is True and data["message_auto_response"] is False
    assert data["members"] == [{"staff_id": 4, "role_id": 2, "alerts": False}]
    assert "flags" not in data


@pytest.mark.asyncio
async def test_department_settings_are_queued_and_validated():
    body = {"parent_id": 1, "sla_id": 2, "status": "disabled", "assignment": "members", "alerts_to": "admin_only",
            "disable_auto_claim": True, "ticket_auto_response": False,
            "members": [{"staff_id": 4, "role_id": 2, "alerts": False}]}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        ok = await ac.patch("/api/v1/departments/2", json=body, headers=_auth(is_admin=True))
        bad_status = await ac.patch("/api/v1/departments/2", json={"status": "gone"}, headers=_auth(is_admin=True))
        bad_alerts = await ac.patch("/api/v1/departments/2", json={"alerts_to": "everyone"}, headers=_auth(is_admin=True))
        dup = await ac.patch("/api/v1/departments/2", json={"members": [
            {"staff_id": 4, "role_id": 2}, {"staff_id": 4, "role_id": 1}]}, headers=_auth(is_admin=True))
        agent = await ac.patch("/api/v1/departments/2", json={"status": "active"}, headers=_auth(["user.edit"]))
    assert (ok.status_code, bad_status.status_code, bad_alerts.status_code, dup.status_code, agent.status_code) == (
        202, 422, 422, 422, 403)
    assert json.loads((await _inbox_rows())[0].payload) == {"dept_id": 2, **body}


@pytest.mark.asyncio
async def test_create_department_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/departments",
            json={"name": "Sales"},
            headers=_auth(is_admin=True),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "dept.create"
    payload = json.loads(rows[0].payload)
    assert payload["name"] == "Sales"


@pytest.mark.asyncio
async def test_update_department_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.patch(
            "/api/v1/departments/2",
            json={"name": "IT Support"},
            headers=_auth(is_admin=True),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "dept.update"
    assert rows[0].aggregate_id == "2"


@pytest.mark.asyncio
async def test_delete_department_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.delete("/api/v1/departments/3", headers=_auth(is_admin=True))
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "dept.delete"
    assert rows[0].aggregate_id == "3"


# ── Teams ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_teams_returns_200():
    with patch("app.modules.staff.service.repo.list_teams", new=AsyncMock(return_value=[MOCK_TEAM])):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/teams", headers=_auth(is_admin=True))
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["name"] == "Level 1 Support"


@pytest.mark.asyncio
async def test_get_team_returns_detail():
    with (
        patch("app.modules.staff.service.repo.get_team", new=AsyncMock(return_value=MOCK_TEAM)),
        patch("app.modules.staff.service.repo.get_team_members", new=AsyncMock(return_value=[{"staff_id": 5, "alerts": True}, {"staff_id": 6, "alerts": False}]))
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/teams/1", headers=_auth(is_admin=True))
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Level 1 Support"
    assert data["members"] == [5, 6]
    assert data["no_alerts"] is True
    assert data["member_alerts"] == {"5": True, "6": False}


@pytest.mark.asyncio
async def test_create_team_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/teams",
            json={"name": "L2 Support", "members": [5]},
            headers=_auth(is_admin=True),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "team.create"
    payload = json.loads(rows[0].payload)
    assert payload["name"] == "L2 Support"
    assert payload["members"] == [5]


@pytest.mark.asyncio
async def test_update_team_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.patch(
            "/api/v1/teams/2",
            json={"name": "Updated Team"},
            headers=_auth(is_admin=True),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "team.update"
    assert rows[0].aggregate_id == "2"


@pytest.mark.asyncio
async def test_delete_team_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.delete("/api/v1/teams/3", headers=_auth(is_admin=True))
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "team.delete"
    assert rows[0].aggregate_id == "3"


# ── Staff ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_staff_returns_200():
    with patch("app.modules.staff.service.repo.list_staff", new=AsyncMock(return_value=[MOCK_STAFF])):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/staff", headers=_auth(is_admin=True))
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["username"] == "agent"


@pytest.mark.asyncio
async def test_get_staff_returns_detail():
    with (
        patch("app.modules.staff.service.repo.get_staff", new=AsyncMock(return_value={
            **MOCK_STAFF, "phone_ext": "12", "notes": "n", "is_visible": 1, "on_vacation": 0, "assigned_only": 1,
            "permissions": '{"user.dir":1,"user.edit":0,"ticket.create":1}', "extra": '{"def_assn_role":true}'})),
        patch("app.modules.staff.service.repo.get_staff_teams", new=AsyncMock(return_value=[1])),
        patch("app.modules.staff.service.repo.get_staff_dept_access",
              new=AsyncMock(return_value=[{"dept_id": 3, "role_id": 2, "alerts": 0}])),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/staff/5", headers=_auth(is_admin=True))
    assert resp.status_code == 200
    data = resp.json()
    assert data["username"] == "agent"
    assert data["teams"] == [1]
    assert data["permissions"] == ["user.dir"]              # only enabled, only global keys
    assert data["assigned_only"] is True and data["assign_use_primary_role"] is True
    assert data["department_access"] == [{"dept_id": 3, "role_id": 2, "alerts": False}]


@pytest.mark.asyncio
async def test_update_staff_access_fields_are_queued_and_validated():
    body = {"permissions": ["user.dir", "user.dir", "faq.manage"], "assigned_only": True, "on_vacation": False,
            "department_access": [{"dept_id": 3, "role_id": 2, "alerts": False}]}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        ok = await ac.patch("/api/v1/staff/5", json=body, headers=_auth(is_admin=True))
        role_perm = await ac.patch("/api/v1/staff/5", json={"permissions": ["ticket.create"]}, headers=_auth(is_admin=True))
        bogus = await ac.patch("/api/v1/staff/5", json={"permissions": ["root"]}, headers=_auth(is_admin=True))
        dup = await ac.patch("/api/v1/staff/5", json={"department_access": [
            {"dept_id": 3, "role_id": 2}, {"dept_id": 3, "role_id": 1}]}, headers=_auth(is_admin=True))
        clear = await ac.patch("/api/v1/staff/5", json={"permissions": [], "department_access": []}, headers=_auth(is_admin=True))
        agent = await ac.patch("/api/v1/staff/5", json={"permissions": ["user.dir"]}, headers=_auth(["user.edit"]))
    assert (ok.status_code, role_perm.status_code, bogus.status_code, dup.status_code, clear.status_code,
            agent.status_code) == (202, 422, 422, 422, 202, 403)
    rows = await _inbox_rows()
    first = json.loads(rows[0].payload)
    assert first["permissions"] == ["faq.manage", "user.dir"] and first["on_vacation"] is False
    assert first["department_access"] == [{"dept_id": 3, "role_id": 2, "alerts": False}]
    assert json.loads(rows[1].payload)["permissions"] == [] and json.loads(rows[1].payload)["department_access"] == []


@pytest.mark.asyncio
async def test_create_staff_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/staff",
            json={
                "username": "new_agent",
                "firstname": "New",
                "lastname": "Agent",
                "email": "new@test.local",
                "dept_id": 1,
                "role_id": 1,
                "password": "secure_password",
            },
            headers=_auth(is_admin=True),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "staff.create"
    payload = json.loads(rows[0].payload)
    assert payload["username"] == "new_agent"


@pytest.mark.asyncio
async def test_update_staff_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.patch(
            "/api/v1/staff/6",
            json={"firstname": "Updated"},
            headers=_auth(is_admin=True),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "staff.update"
    assert rows[0].aggregate_id == "6"


@pytest.mark.asyncio
async def test_delete_staff_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.delete("/api/v1/staff/7", headers=_auth(is_admin=True))
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "staff.delete"
    assert rows[0].aggregate_id == "7"


# ── Common checks ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_all_endpoints_require_auth():
    endpoints = [
        ("GET",    "/api/v1/departments"),
        ("GET",    "/api/v1/departments/1"),
        ("POST",   "/api/v1/departments"),
        ("PATCH",  "/api/v1/departments/1"),
        ("DELETE", "/api/v1/departments/1"),
        ("GET",    "/api/v1/teams"),
        ("GET",    "/api/v1/teams/1"),
        ("POST",   "/api/v1/teams"),
        ("PATCH",  "/api/v1/teams/1"),
        ("DELETE", "/api/v1/teams/1"),
        ("GET",    "/api/v1/staff"),
        ("GET",    "/api/v1/staff/1"),
        ("POST",   "/api/v1/staff"),
        ("PATCH",  "/api/v1/staff/1"),
        ("DELETE", "/api/v1/staff/1"),
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        for method, path in endpoints:
            func = getattr(ac, method.lower())
            kwargs = {} if method in ("GET", "DELETE") else {"json": {}}
            resp = await func(path, **kwargs)
            assert resp.status_code == 401, f"{method} {path} → {resp.status_code}"
