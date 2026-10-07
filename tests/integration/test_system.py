"""System logs and system information: Admin Panel pages."""

import json
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import select

from app.integration.osticket.commands import IntegrationInbox
from app.main import app
from tests.conftest import TestingSessionLocal
from tests.integration.test_tasks import _auth

NOW = "2026-10-07T10:00:00"
ROW = {"id": 7, "type": "Error", "title": "boom", "logger": "Cron", "ip_address": "10.0.0.1", "created": NOW, "updated": NOW, "log": "details"}


@pytest.mark.asyncio
async def test_logs_are_listed_read_and_deleted_by_administrators_only():
    list_logs, count = AsyncMock(return_value=[ROW]), AsyncMock(return_value=1)
    with patch("app.modules.system.service.repo.list_logs", new=list_logs), \
         patch("app.modules.system.service.repo.count_logs", new=count), \
         patch("app.modules.system.service.repo.get_log", new=AsyncMock(side_effect=lambda s, i: ROW if i == 7 else None)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            admin, agent = _auth(is_admin=True), _auth(["ticket.reply"])
            ok = await ac.get("/api/v1/logs?type=Error&since=2026-10-01T00:00:00Z&sort=created", headers=admin)
            denied = await ac.get("/api/v1/logs", headers=agent)
            one = await ac.get("/api/v1/logs/7", headers=admin)
            missing = await ac.get("/api/v1/logs/8", headers=admin)
            reversed_span = await ac.get("/api/v1/logs?since=2026-10-02T00:00:00Z&until=2026-10-01T00:00:00Z", headers=admin)
            bad_type = await ac.get("/api/v1/logs?type=Info", headers=admin)
            queued = await ac.post("/api/v1/logs/delete", json={"ids": [3, 3, 1]}, headers=admin)
            empty = await ac.post("/api/v1/logs/delete", json={"ids": []}, headers=admin)
    assert (ok.status_code, denied.status_code, one.status_code, missing.status_code) == (200, 403, 200, 404)
    assert (reversed_span.status_code, bad_type.status_code, queued.status_code, empty.status_code) == (422, 422, 202, 422)
    assert ok.json()["total"] == 1 and ok.json()["items"][0]["type"] == "Error"
    assert one.json()["log"] == "details"
    assert list_logs.await_args.kwargs["log_type"] == "Error" and list_logs.await_args.kwargs["sort"] == "created"
    async with TestingSessionLocal() as s:
        (row,) = (await s.execute(select(IntegrationInbox))).scalars().all()
    assert row.command_type == "log.delete" and json.loads(row.payload) == {"ids": [1, 3]}


@pytest.mark.asyncio
async def test_system_info_comes_from_osticket_for_administrators_only():
    info = {
        "osticket_version": "v1.18", "web_server": "Apache", "mysql_version": "10.11", "php_version": "8.2",
        "extensions": [{"key": "gd", "name": "gdlib", "description": "d", "loaded": True}],
        "php_settings": [{"name": "date.timezone", "value": "UTC", "ok": True}],
        "schema_name": "osticket", "schema_signature": "abc", "space_used_mib": 12.5, "attachment_space_mib": 0.1,
        "database_timezone": "UTC", "interpreted_timezone": "UTC", "language_packs": [{"code": "en_US", "name": "English"}],
    }
    bridge = AsyncMock(return_value=info)
    with patch("app.modules.system.service.bridge_json", new=bridge):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            ok = await ac.get("/api/v1/system/info", headers=_auth(is_admin=True))
            denied = await ac.get("/api/v1/system/info", headers=_auth([]))
    assert (ok.status_code, denied.status_code) == (200, 403)
    assert ok.json()["osticket_version"] == "v1.18" and ok.json()["language_packs"][0]["version"] is None
    assert bridge.await_args.args[0] == "system.info"
