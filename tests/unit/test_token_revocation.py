"""Changing a password ends the sessions that were opened before it."""

import dataclasses
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.core import auth
from app.core.access import build_access_context
from app.core.ws_manager import ConnectionManager
from tests.conftest import make_access
from tests.unit.test_ws_manager import FakeSocket

CHANGED = 1_000_000


def test_a_token_issued_before_the_password_changed_is_void():
    access = dataclasses.replace(make_access(1, []), password_changed_at=CHANGED)
    assert not access.token_is_current(CHANGED - 1)
    assert access.token_is_current(CHANGED) and access.token_is_current(CHANGED + 500)
    assert make_access(1, []).token_is_current(0)                     # never changed: every token stands


def test_the_staff_row_carries_the_change_time_into_the_context():
    rows = {"staff": {"staff_id": 1, "dept_id": 1, "role_id": 1, "isactive": 1, "isadmin": 0, "assigned_only": 0,
                      "passwd_changed": CHANGED}, "roles": {}, "dept_access": [], "dept_ids": [1], "team_ids": []}
    assert build_access_context(rows).password_changed_at == CHANGED
    rows["staff"]["passwd_changed"] = None
    assert build_access_context(rows).password_changed_at == 0


@pytest.mark.asyncio
async def test_rest_refuses_an_old_token_and_accepts_a_new_one():
    stale = auth.create_access_token({"staff_id": 1}, expires_delta=timedelta(minutes=5))
    iat = auth.decode_access_token(stale)["iat"]
    changed_later = dataclasses.replace(make_access(1, []), password_changed_at=iat + 5)
    with patch("app.core.auth.load_access_context", new=AsyncMock(return_value=changed_later)):
        with pytest.raises(HTTPException) as refused:
            await auth.get_current_user(stale, session=None)
        assert refused.value.status_code == 401 and "password changed" in refused.value.detail
        with pytest.raises(HTTPException):
            await auth.authenticate_websocket(stale)
    changed_before = dataclasses.replace(make_access(1, []), password_changed_at=iat - 5)
    with patch("app.core.auth.load_access_context", new=AsyncMock(return_value=changed_before)):
        assert (await auth.get_current_user(stale, session=None))["staff_id"] == 1


@pytest.mark.asyncio
async def test_an_open_socket_is_closed_when_the_password_changes_under_it():
    manager = ConnectionManager()
    ws = FakeSocket()
    await manager.connect(ws, make_access(1, []), 10**12, issued_at=CHANGED - 10)

    async def changed(_staff_id):
        return dataclasses.replace(make_access(1, []), password_changed_at=CHANGED)

    summary = await manager.revalidate(changed)
    assert summary["deactivated"] == 1 and manager.client_count == 0 and ws.closed
