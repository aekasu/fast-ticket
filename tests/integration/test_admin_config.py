"""API keys and site pages: Admin Panel pages, so admin only, every write queued as a command with only what was sent."""

import json

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
