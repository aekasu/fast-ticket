"""Integration tests for Forms endpoints (Phase 10)."""

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

MOCK_FORM = {
    "id": 1,
    "title": "Contact Information",
    "type": "U",
    "is_deletable": False,
    "instructions": "Please provide details.",
    "notes": "User form",
    "created": _NOW,
    "updated": _NOW,
}

MOCK_FIELDS = [
    {
        "id": 1,
        "name": "phone",
        "label": "Phone Number",
        "type": "text",
        "is_required": False,
    },
]


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


# ── Forms ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_forms_returns_200():
    with patch("app.modules.forms.service.repo.list_forms", new=AsyncMock(return_value=[MOCK_FORM])):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/forms", headers=_auth(is_admin=True))
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["title"] == "Contact Information"


@pytest.mark.asyncio
async def test_get_form_returns_detail():
    with (
        patch("app.modules.forms.service.repo.get_form", new=AsyncMock(return_value=MOCK_FORM)),
        patch("app.modules.forms.service.repo.get_form_fields", new=AsyncMock(return_value=MOCK_FIELDS))
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/forms/1", headers=_auth(is_admin=True))
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Contact Information"
    assert len(data["fields"]) == 1
    assert data["fields"][0]["name"] == "phone"


@pytest.mark.asyncio
async def test_create_form_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/forms",
            json={"title": "Asset Detail", "type": "T"},
            headers=_auth(is_admin=True),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "form.create"
    payload = json.loads(rows[0].payload)
    assert payload["title"] == "Asset Detail"


@pytest.mark.asyncio
async def test_update_form_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.patch(
            "/api/v1/forms/2",
            json={"title": "Updated Title"},
            headers=_auth(is_admin=True),
        )
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "form.update"
    assert rows[0].aggregate_id == "2"


@pytest.mark.asyncio
async def test_delete_form_queues_command():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.delete("/api/v1/forms/3", headers=_auth(is_admin=True))
    assert resp.status_code == 202
    rows = await _inbox_rows()
    assert rows[0].command_type == "form.delete"
    assert rows[0].aggregate_id == "3"


# ── Common checks ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_all_endpoints_require_auth():
    endpoints = [
        ("GET",    "/api/v1/forms"),
        ("GET",    "/api/v1/forms/1"),
        ("POST",   "/api/v1/forms"),
        ("PATCH",  "/api/v1/forms/1"),
        ("DELETE", "/api/v1/forms/1"),
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        for method, path in endpoints:
            func = getattr(ac, method.lower())
            kwargs = {} if method in ("GET", "DELETE") else {"json": {}}
            resp = await func(path, **kwargs)
            assert resp.status_code == 401, f"{method} {path} → {resp.status_code}"


# ── fields ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_form_detail_decodes_field_flags_and_configuration():
    from app.modules.forms.service import FIELD_MODES
    rows = [
        {"id": 7, "name": "asset", "label": "Asset tag", "type": "text", "is_required": True, "sort": 3,
         "hint": "scan it", "flags": 1 | FIELD_MODES["required_for_agents"],
         "configuration": '{"size":20,"length":40,"validator":"regex","regex":"^A","validator-error":"bad","junk":1}'},
        {"id": 8, "name": "subject", "label": "Issue", "type": "text", "is_required": False, "sort": 1, "hint": "",
         "flags": 1 | 0x10 | 0x20 | 0x40000 | FIELD_MODES["optional"] | 0x8000000, "configuration": None},
        {"id": 9, "name": "odd", "label": "Odd", "type": "bool", "is_required": False, "sort": 2, "hint": "",
         "flags": 0x100, "configuration": "not json"},
    ]
    with patch("app.modules.forms.service.repo.get_form", new=AsyncMock(return_value=MOCK_FORM)), \
         patch("app.modules.forms.service.repo.get_form_fields", new=AsyncMock(return_value=rows)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            fields = (await ac.get("/api/v1/forms/1", headers=_auth(is_admin=True))).json()["fields"]
    a, core, odd = fields
    assert (a["mode"], a["is_enabled"], a["is_deletable"], a["sort"], a["hint"]) == ("required_for_agents", True, True, 3, "scan it")
    assert a["configuration"]["size"] == 20 and a["configuration"]["validator"] == "regex"
    assert a["configuration"]["validator_error"] == "bad" and "junk" not in a["configuration"]
    assert (core["is_deletable"], core["is_type_locked"], core["is_name_locked"], core["mode"]) == (False, True, True, "optional")
    assert odd["mode"] is None and odd["is_enabled"] is False and odd["configuration"]["size"] is None


@pytest.mark.asyncio
async def test_field_commands_are_queued_validated_and_admin_only():
    add = {"label": "Asset tag", "type": "text", "name": "asset", "mode": "required",
           "configuration": {"size": 20, "validator": "regex", "regex": "^A", "validator_error": "bad"}}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        ok = await ac.post("/api/v1/forms/5/fields", json=add, headers=_auth(is_admin=True))
        upd = await ac.patch("/api/v1/forms/5/fields/9", json={"mode": "internal_optional", "is_enabled": False},
                             headers=_auth(is_admin=True))
        rem = await ac.delete("/api/v1/forms/5/fields/9?delete_data=true", headers=_auth(is_admin=True))
        rem2 = await ac.delete("/api/v1/forms/5/fields/9", headers=_auth(is_admin=True))
        bad = [
            await ac.post("/api/v1/forms/5/fields", json={**add, "type": "nonsense"}, headers=_auth(is_admin=True)),
            await ac.post("/api/v1/forms/5/fields", json={**add, "name": "9starts"}, headers=_auth(is_admin=True)),
            await ac.post("/api/v1/forms/5/fields", json={**add, "mode": "sometimes"}, headers=_auth(is_admin=True)),
            await ac.post("/api/v1/forms/5/fields", json={**add, "configuration": {"bogus": 1}}, headers=_auth(is_admin=True)),
            await ac.post("/api/v1/forms/5/fields", json={**add, "configuration": {"size": 0}}, headers=_auth(is_admin=True)),
            await ac.patch("/api/v1/forms/5/fields/9", json={}, headers=_auth(is_admin=True)),
        ]
        agent = await ac.post("/api/v1/forms/5/fields", json=add, headers=_auth(["faq.manage"]))
    assert [r.status_code for r in (ok, upd, rem, rem2)] == [202] * 4
    assert [r.status_code for r in bad] == [422] * 6 and agent.status_code == 403
    rows = await _inbox_rows()
    assert [r.command_type for r in rows] == ["form.field.add", "form.field.update", "form.field.delete", "form.field.delete"]
    payload = json.loads(rows[0].payload)
    assert payload["configuration"] == {"size": 20, "validator": "regex", "regex": "^A", "validator-error": "bad"}
    assert json.loads(rows[2].payload) == {"form_id": 5, "field_id": 9, "delete_data": True}
    assert json.loads(rows[3].payload)["delete_data"] is False


@pytest.mark.asyncio
async def test_object_forms_are_open_to_any_agent_and_skip_what_the_topic_disables():
    forms = AsyncMock(return_value=[
        {"id": 2, "title": "Ticket Details", "type": "T", "instructions": "", "extra": None},
        {"id": 9, "title": "Topic form", "type": "G", "instructions": "fill me", "extra": '{"disable":[31]}'}])
    fields = {2: [{"id": 20, "name": "subject", "label": "Subject", "type": "text", "is_required": 1, "is_editable": 1,
                   "sort": 1, "hint": "", "flags": 0x7371, "configuration": "{}"}],
              9: [{"id": 30, "name": "kind", "label": "Kind", "type": "list-3", "is_required": 0, "is_editable": 0,
                   "sort": 1, "hint": "", "flags": 0x1371, "configuration": None},
                  {"id": 31, "name": "off", "label": "Off", "type": "text", "is_required": 0, "is_editable": 1,
                   "sort": 2, "hint": "", "flags": 0x3371, "configuration": "{}"}]}
    async def agent_fields(session, form_id):
        return fields[form_id]
    items = AsyncMock(return_value=[{"id": 1, "value": "Alpha"}])
    with patch("app.modules.forms.service.repo.get_object_forms", new=forms), \
         patch("app.modules.forms.service.repo.get_agent_form_fields", new=agent_fields), \
         patch("app.modules.forms.service.repo.get_list_items", new=items):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            ok = await ac.get("/api/v1/forms/for/ticket", params={"topic_id": 4}, headers=_auth([]))
            bad = await ac.get("/api/v1/forms/for/widget", headers=_auth([]))
            anon = await ac.get("/api/v1/forms/for/ticket")
    assert (ok.status_code, bad.status_code, anon.status_code) == (200, 422, 401)
    assert forms.await_args.kwargs == {"kind": "ticket", "topic_id": 4}
    body = ok.json()["forms"]
    assert [f["id"] for f in body] == [2, 9] and body[1]["instructions"] == "fill me"
    assert [f["name"] for f in body[1]["fields"]] == ["kind"]                 # the topic switched "off" off
    kind = body[1]["fields"][0]
    assert (kind["is_editable"], kind["list_items"]) == (False, [{"id": 1, "value": "Alpha"}])
    assert body[0]["fields"][0]["is_required"] is True and body[0]["fields"][0]["list_items"] is None
