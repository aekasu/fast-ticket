"""GET /roles (admin) and GET /permissions (any agent)."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.integration.test_tasks import _auth

URL = "/api/v1"


@pytest.mark.asyncio
async def test_roles_list_parses_permissions_and_is_admin_only():
    rows = [{"id": 2, "name": "Expanded Access", "notes": None, "enabled": 1,
             "permissions": '{"ticket.edit":1,"ticket.close":0,"bogus.key":1}'},
            {"id": 4, "name": "View only", "notes": "", "enabled": 0, "permissions": None}]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        with patch("app.modules.roles.service.repo.list_roles", new=AsyncMock(return_value=rows)):
            ok = await ac.get(URL + "/roles", headers=_auth(is_admin=True))
        denied = await ac.get(URL + "/roles", headers=_auth(["ticket.edit"]))
        anon = await ac.get(URL + "/roles")
    assert (ok.status_code, denied.status_code, anon.status_code) == (200, 403, 401)
    items = ok.json()["items"]
    assert items[0] == {"id": 2, "name": "Expanded Access", "notes": "", "enabled": True, "permissions": ["ticket.edit"]}
    assert items[1]["enabled"] is False and items[1]["permissions"] == []


@pytest.mark.asyncio
async def test_permission_keys_are_open_to_any_agent():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        ok = await ac.get(URL + "/permissions", headers=_auth([]))
        anon = await ac.get(URL + "/permissions")
    assert (ok.status_code, anon.status_code) == (200, 401)
    keys = {p["key"]: p["kind"] for p in ok.json()["permissions"]}
    assert keys["user.dir"] == "global" and keys["ticket.edit"] == "role"
