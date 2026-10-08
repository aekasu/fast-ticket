"""Ticket statuses: the Admin Panel's list (admin only) and its create / update / delete commands."""

import json
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import select

from app.integration.osticket.commands import IntegrationInbox
from app.main import app
from tests.conftest import TestingSessionLocal
from tests.integration.test_tasks import _auth

URL = "/api/v1/ticket-statuses"
ROWS = [
    {"id": 1, "name": "Open", "state": "open", "mode": 3, "sort": 1, "properties": '{"description":"Open tickets."}', "ticket_count": 4},
    {"id": 2, "name": "Resolved", "state": "closed", "mode": 1, "sort": 2,
     "properties": '{"allowreopen":true,"reopenstatus":1,"description":"Done"}', "ticket_count": 0},
    {"id": 6, "name": "Waiting", "state": "open", "mode": 0, "sort": 6, "properties": "", "ticket_count": 0},
]


@pytest.mark.asyncio
async def test_the_admin_list_shows_every_status_with_what_decides_whether_it_can_change():
    with patch("app.modules.lookups.service.repo.list_ticket_status_details", new=AsyncMock(return_value=ROWS)), \
         patch("app.modules.lookups.service.repo.get_ticket_status_detail", new=AsyncMock(side_effect=lambda s, i: next((r for r in ROWS if r["id"] == i), None))), \
         patch("app.modules.lookups.service.repo.get_core_config", new=AsyncMock(return_value={"default_ticket_status_id": "1"})):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            admin, agent = _auth(is_admin=True), _auth(["ticket.reply"])
            listed = await ac.get(URL + "/manage", headers=admin)
            denied = await ac.get(URL + "/manage", headers=agent)
            one = await ac.get(URL + "/2", headers=admin)
            missing = await ac.get(URL + "/99", headers=admin)
    assert (listed.status_code, denied.status_code, one.status_code, missing.status_code) == (200, 403, 200, 404)
    by_id = {s["id"]: s for s in listed.json()["items"]}
    assert by_id[1]["is_internal"] and by_id[1]["is_default"] and not by_id[1]["is_deletable"] and by_id[1]["ticket_count"] == 4
    assert by_id[2]["allow_reopen"] is True and by_id[2]["reopen_status_id"] == 1 and by_id[2]["is_enabled"] and by_id[2]["is_deletable"]
    assert by_id[6]["is_enabled"] is False and by_id[6]["description"] == "" and by_id[6]["reopen_status_id"] is None
    assert one.json()["description"] == "Done"


@pytest.mark.asyncio
async def test_writes_queue_commands_for_administrators_only_and_validate_the_body():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        admin, agent = _auth(is_admin=True), _auth(["ticket.close"])
        made = await ac.post(URL, json={"name": "Waiting on vendor", "state": "open", "description": "Parked"}, headers={**admin, "Idempotency-Key": "k1"})
        again = await ac.post(URL, json={"name": "Waiting on vendor", "state": "open", "description": "Parked"}, headers={**admin, "Idempotency-Key": "k1"})
        changed = await ac.patch(URL + "/6", json={"allow_reopen": True, "reopen_status_id": 1, "is_enabled": False}, headers=admin)
        removed = await ac.delete(URL + "/6", headers=admin)
        denied = [await ac.post(URL, json={"name": "x", "state": "open"}, headers=agent),
                  await ac.patch(URL + "/6", json={"name": "x"}, headers=agent), await ac.delete(URL + "/6", headers=agent)]
        bad_state = await ac.post(URL, json={"name": "x", "state": "pending"}, headers=admin)
        no_name = await ac.post(URL, json={"name": "", "state": "open"}, headers=admin)
        long_name = await ac.post(URL, json={"name": "x" * 61, "state": "open"}, headers=admin)
        anon = await ac.delete(URL + "/6")
    assert (made.status_code, again.status_code, changed.status_code, removed.status_code) == (202, 202, 202, 202)
    assert made.json()["command_id"] == again.json()["command_id"]
    assert [r.status_code for r in denied] == [403, 403, 403] and (bad_state.status_code, no_name.status_code, long_name.status_code, anon.status_code) == (422, 422, 422, 401)
    async with TestingSessionLocal() as s:
        rows = (await s.execute(select(IntegrationInbox).order_by(IntegrationInbox.id))).scalars().all()
    assert [r.command_type for r in rows] == ["ticket_status.create", "ticket_status.update", "ticket_status.delete"]
    assert json.loads(rows[0].payload) == {"name": "Waiting on vendor", "state": "open", "description": "Parked", "allow_reopen": False, "is_enabled": True}
    assert json.loads(rows[1].payload) == {"status_id": 6, "allow_reopen": True, "reopen_status_id": 1, "is_enabled": False}
    assert (rows[1].aggregate_type, rows[1].aggregate_id, rows[2].aggregate_id) == ("ticket_status", "6", "6")
