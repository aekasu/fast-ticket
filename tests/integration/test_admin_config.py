"""API keys and site pages: Admin Panel pages, so admin only, every write queued as a command with only what was sent."""

import json
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import select

from app.integration.osticket.commands import IntegrationInbox
from app.main import app
from tests.conftest import TestingSessionLocal
from tests.integration.test_tasks import _auth


async def _rows():
    async with TestingSessionLocal() as s:
        return (await s.execute(select(IntegrationInbox).order_by(IntegrationInbox.id))).scalars().all()


@pytest.mark.asyncio
async def test_api_key_writes_are_admin_only_commands_with_only_what_was_sent():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        admin, agent = _auth(is_admin=True), _auth(["ticket.create"])
        made = await ac.post("/api/v1/api-keys", json={"ip_address": "10.1.2.3", "can_exec_cron": True}, headers=admin)
        changed = await ac.patch("/api/v1/api-keys/4", json={"is_active": False}, headers=admin)
        removed = await ac.delete("/api/v1/api-keys/4", headers=admin)
        denied = await ac.post("/api/v1/api-keys", json={"ip_address": "10.1.2.3"}, headers=agent)
        short = await ac.post("/api/v1/api-keys", json={"ip_address": "1"}, headers=admin)
    assert (made.status_code, changed.status_code, removed.status_code, denied.status_code, short.status_code) == (202, 202, 202, 403, 422)
    rows = await _rows()
    assert [r.command_type for r in rows] == ["apikey.create", "apikey.update", "apikey.delete"]
    assert json.loads(rows[0].payload)["ip_address"] == "10.1.2.3"
    assert json.loads(rows[1].payload) == {"key_id": 4, "is_active": False}


@pytest.mark.asyncio
async def test_page_writes_validate_the_type_and_send_only_what_changed():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        admin = _auth(is_admin=True)
        made = await ac.post("/api/v1/pages", json={"name": "FAQ", "type": "other", "body": "<p>x</p>"}, headers=admin)
        bad_type = await ac.post("/api/v1/pages", json={"name": "FAQ", "type": "registration-staff", "body": "x"}, headers=admin)
        no_body = await ac.post("/api/v1/pages", json={"name": "FAQ", "type": "other", "body": ""}, headers=admin)
        changed = await ac.patch("/api/v1/pages/9", json={"is_active": False}, headers=admin)
        removed = await ac.delete("/api/v1/pages/9", headers=admin)
    assert (made.status_code, bad_type.status_code, no_body.status_code, changed.status_code, removed.status_code) == (202, 422, 422, 202, 202)
    rows = await _rows()
    assert [r.command_type for r in rows] == ["page.create", "page.update", "page.delete"]
    assert json.loads(rows[1].payload) == {"page_id": 9, "is_active": False}


@pytest.mark.asyncio
async def test_custom_list_and_item_writes_are_queued_with_only_what_was_sent():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        admin = _auth(is_admin=True)
        made = await ac.post("/api/v1/lists", json={"name": "Colours", "sort_mode": "SortCol"}, headers=admin)
        bad_mode = await ac.post("/api/v1/lists", json={"name": "Colours", "sort_mode": "Random"}, headers=admin)
        renamed = await ac.patch("/api/v1/lists/3", json={"notes": "n"}, headers=admin)
        item = await ac.post("/api/v1/lists/3/items", json={"value": "Red", "abbrev": "R"}, headers=admin)
        edited = await ac.patch("/api/v1/lists/3/items/7", json={"is_enabled": False}, headers=admin)
        removed = await ac.delete("/api/v1/lists/3/items/7", headers=admin)
        dropped = await ac.delete("/api/v1/lists/3", headers=admin)
    assert [r.status_code for r in (made, bad_mode, renamed, item, edited, removed, dropped)] == [202, 422, 202, 202, 202, 202, 202]
    rows = await _rows()
    assert [r.command_type for r in rows] == ["list.create", "list.update", "list.item.create", "list.item.update", "list.item.delete", "list.delete"]
    assert json.loads(rows[1].payload) == {"list_id": 3, "notes": "n"}
    assert json.loads(rows[3].payload) == {"list_id": 3, "item_id": 7, "is_enabled": False}


@pytest.mark.asyncio
async def test_settings_pages_are_admin_only_and_queue_only_what_was_sent():
    stored = {"autolock_minutes": "3", "enable_kb": "", "isonline": "1", "secondary_langs": "de, fr"}
    with patch("app.modules.settings.service.repo.get_core_config", new=AsyncMock(return_value=stored)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            admin, agent = _auth(is_admin=True), _auth(["ticket.edit"])
            system = await ac.get("/api/v1/settings/system", headers=admin)
            kb = await ac.get("/api/v1/settings/kb", headers=admin)
            denied = await ac.get("/api/v1/settings/system", headers=agent)
            changed = await ac.patch("/api/v1/settings/system", json={"autolock_minutes": 5, "enable_avatars": True}, headers=admin)
            wrong_section_key = await ac.patch("/api/v1/settings/kb", json={"autolock_minutes": 5}, headers=admin)
            wrong_type = await ac.patch("/api/v1/settings/system", json={"autolock_minutes": "soon"}, headers=admin)
            negative = await ac.patch("/api/v1/settings/system", json={"max_page_size": -1}, headers=admin)
    assert (system.status_code, kb.status_code, denied.status_code, changed.status_code) == (200, 200, 403, 202)
    assert system.json()["autolock_minutes"] == 3 and system.json()["isonline"] is True
    assert system.json()["secondary_langs"] == ["de", "fr"] and kb.json() == {"enable_kb": False, "restrict_kb": False, "enable_premade": False}
    assert wrong_section_key.status_code == 202            # an unknown key is ignored by the model; the bridge refuses a wrong-section key
    assert (wrong_type.status_code, negative.status_code) == (422, 422)
    rows = await _rows()
    assert json.loads(rows[0].payload) == {"section": "system", "values": {"autolock_minutes": 5, "enable_avatars": True}}
    assert json.loads(rows[1].payload) == {"section": "kb", "values": {}}


@pytest.mark.asyncio
async def test_email_template_writes_are_queued_and_unknown_messages_are_404():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        admin = _auth(is_admin=True)
        made = await ac.post("/api/v1/email-templates", json={"name": "Mine", "copy_from_id": 1}, headers=admin)
        renamed = await ac.patch("/api/v1/email-templates/2", json={"is_active": False}, headers=admin)
        text = await ac.put("/api/v1/email-templates/2/messages/ticket.autoresp", json={"subject": "s", "body": "<p>b</p>"}, headers=admin)
        unknown = await ac.put("/api/v1/email-templates/2/messages/not.a.message", json={"subject": "s", "body": "b"}, headers=admin)
        empty = await ac.put("/api/v1/email-templates/2/messages/ticket.autoresp", json={"subject": "", "body": "b"}, headers=admin)
        dropped = await ac.delete("/api/v1/email-templates/2", headers=admin)
    assert [r.status_code for r in (made, renamed, text, unknown, empty, dropped)] == [202, 202, 202, 404, 422, 202]
    rows = await _rows()
    assert [r.command_type for r in rows] == ["emailtemplate.create", "emailtemplate.update", "emailtemplate.message.update", "emailtemplate.delete"]
    assert json.loads(rows[1].payload) == {"set_id": 2, "is_active": False}
    assert json.loads(rows[2].payload) == {"set_id": 2, "code_name": "ticket.autoresp", "subject": "s", "body": "<p>b</p>"}


@pytest.mark.asyncio
async def test_email_address_writes_are_admin_commands_with_only_what_was_sent():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        admin = _auth(is_admin=True)
        made = await ac.post("/api/v1/emails", json={"email": "help@example.com", "name": "Help", "dept_id": 2}, headers=admin)
        bad = await ac.post("/api/v1/emails", json={"email": "not-an-address", "name": "Help"}, headers=admin)
        changed = await ac.patch("/api/v1/emails/4", json={"no_autoresponse": True}, headers=admin)
        removed = await ac.delete("/api/v1/emails/4", headers=admin)
        denied = await ac.delete("/api/v1/emails/4", headers=_auth(["ticket.edit"]))
    assert [r.status_code for r in (made, bad, changed, removed, denied)] == [202, 422, 202, 202, 403]
    rows = await _rows()
    assert json.loads(rows[1].payload) == {"email_id": 4, "no_autoresponse": True}


@pytest.mark.asyncio
async def test_filter_writes_validate_rules_and_the_fields_each_action_needs():
    rule = {"what": "subject", "how": "contains", "val": "urgent"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        admin = _auth(is_admin=True)
        ok = await ac.post("/api/v1/filters", json={"name": "Urgent", "rules": [rule], "actions": [
            {"type": "pri", "priority_id": 3}, {"type": "reject"}]}, headers=admin)
        no_rules = await ac.post("/api/v1/filters", json={"name": "x", "rules": []}, headers=admin)
        bad_how = await ac.post("/api/v1/filters", json={"name": "x", "rules": [{**rule, "how": "sounds like"}]}, headers=admin)
        missing = await ac.post("/api/v1/filters", json={"name": "x", "rules": [rule], "actions": [{"type": "dept"}]}, headers=admin)
        stray = await ac.post("/api/v1/filters", json={"name": "x", "rules": [rule], "actions": [{"type": "reject", "dept_id": 1}]}, headers=admin)
        patched = await ac.patch("/api/v1/filters/3", json={"is_active": False}, headers=admin)
        denied = await ac.patch("/api/v1/filters/3", json={"is_active": False}, headers=_auth(["ticket.edit"]))
    assert [r.status_code for r in (ok, no_rules, bad_how, missing, stray, patched, denied)] == [202, 422, 422, 422, 422, 202, 403]
    rows = await _rows()
    created = json.loads(rows[0].payload)
    assert created["actions"] == [{"type": "pri", "priority": 3}, {"type": "reject"}]      # osTicket's own key for the priority
    assert json.loads(rows[1].payload) == {"filter_id": 3, "is_active": False}                   # no rules / actions: kept as stored
