"""Files on thread entries: listed on the entry, downloaded through the API, scoped like the thread."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.integration.test_staff import _auth

ROW = {"id": 4, "entry_id": 11, "name": "report ünïcode.pdf", "size": 6, "content_type": "application/pdf",
       "inline": False, "file_id": 70, "backend": "D"}


async def _chunks(file_id):
    assert file_id == 70
    yield b"abc"
    yield b"def"


async def _get(path, headers=None):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        return await ac.get(path, headers=headers or {})


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,target", [
    ("tickets", "app.modules.tickets.service.repo.get_ticket_attachments"),
    ("tasks", "app.modules.tasks.service.repo.get_task_attachments"),
])
async def test_download_streams_the_file_as_an_attachment(kind, target):
    with patch(target, new=AsyncMock(return_value=[ROW])) as lookup, patch("app.core.attachments._chunks", new=_chunks):
        resp = await _get(f"/api/v1/{kind}/9/attachments/4", _auth())
    assert resp.status_code == 200 and resp.content == b"abcdef"
    assert resp.headers["content-type"].startswith("application/pdf") and resp.headers["content-length"] == "6"
    disposition = resp.headers["content-disposition"]
    assert disposition.startswith("attachment;") and "filename*=UTF-8''report%20%C3%BCn%C3%AFcode.pdf" in disposition
    assert lookup.call_args.kwargs["attachment_id"] == 4 and lookup.call_args.kwargs["access"].staff_id == 5


@pytest.mark.asyncio
async def test_a_file_that_is_not_attached_to_a_ticket_the_agent_can_open_is_404():
    with patch("app.modules.tickets.service.repo.get_ticket_attachments", new=AsyncMock(return_value=[])):
        assert (await _get("/api/v1/tickets/9/attachments/4", _auth())).status_code == 404
    assert (await _get("/api/v1/tickets/9/attachments/4")).status_code == 401


class _Upstream:
    """What bridge_read.open_bridge_stream hands back: a client and a streaming response."""

    def __init__(self, chunks):
        self.chunks, self.closed = chunks, []

    async def aiter_raw(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self):
        self.closed.append("response")


@pytest.mark.asyncio
async def test_a_file_kept_by_a_storage_plugin_is_read_through_the_bridge():
    upstream = _Upstream([b"abc", b"def"])
    client = type("C", (), {"aclose": AsyncMock()})()
    opened = AsyncMock(return_value=(client, upstream))
    with patch("app.modules.tickets.service.repo.get_ticket_attachments", new=AsyncMock(return_value=[{**ROW, "backend": "S"}])), \
            patch("app.core.attachments.open_bridge_stream", new=opened):
        resp = await _get("/api/v1/tickets/9/attachments/4", _auth())
    assert resp.status_code == 200 and resp.content == b"abcdef" and resp.headers["content-length"] == "6"
    assert opened.await_args.args == ("file.stream", 5, {"object_type": "T", "object_id": 9, "attachment_id": 4})
    assert upstream.closed == ["response"] and client.aclose.await_count == 1        # nothing left open


@pytest.mark.asyncio
async def test_the_bridges_refusals_reach_the_client_as_osticket_means_them():
    from fastapi import HTTPException
    for status, expected in ((501, 501), (404, 404), (503, 503)):
        refuse = AsyncMock(side_effect=HTTPException(status_code=status, detail="x"))
        with patch("app.modules.tickets.service.repo.get_ticket_attachments", new=AsyncMock(return_value=[{**ROW, "backend": "S"}])), \
                patch("app.core.attachments.open_bridge_stream", new=refuse):
            assert (await _get("/api/v1/tickets/9/attachments/4", _auth())).status_code == expected


@pytest.mark.asyncio
async def test_a_hostile_type_becomes_octet_stream():
    hostile = {**ROW, "content_type": "text/html\r\nSet-Cookie: x=1", "name": 'a"\r\nb.html'}
    with patch("app.modules.tickets.service.repo.get_ticket_attachments", new=AsyncMock(return_value=[hostile])), \
            patch("app.core.attachments._chunks", new=_chunks):
        resp = await _get("/api/v1/tickets/9/attachments/4", _auth())
    assert resp.status_code == 200 and resp.headers["content-type"] == "application/octet-stream"
    assert "set-cookie" not in resp.headers and "\n" not in resp.headers["content-disposition"]


@pytest.mark.asyncio
async def test_the_thread_lists_each_entrys_files():
    row = {"ticket_id": 9, "number": "1", "subject": "s", "status_id": 1, "status_name": "Open", "status_state": "open",
           "dept_id": 1, "dept_name": "Support", "created": "2024-01-01T00:00:00", "updated": "2024-01-01T00:00:00"}
    entry = {"id": 11, "type": "M", "title": None, "body": "b", "staff_id": 0, "user_id": 3, "poster": "U",
             "ip_address": None, "created": "2024-01-01T00:00:00", "is_edited": False}
    with patch("app.modules.tickets.service.repo.get_ticket", new=AsyncMock(return_value=row)), \
            patch("app.modules.tickets.service.repo.get_ticket_thread", new=AsyncMock(return_value=[entry, {**entry, "id": 12}])), \
            patch("app.modules.tickets.service.repo.get_ticket_attachments", new=AsyncMock(return_value=[ROW])), \
            patch("app.modules.tickets.service.repo.get_ticket_collaborators", new=AsyncMock(return_value=[])), \
            patch("app.modules.tickets.service.repo.get_ticket_custom_fields", new=AsyncMock(return_value=[])):
        resp = await _get("/api/v1/tickets/9", _auth())
    thread = resp.json()["thread"]
    assert thread[0]["attachments"] == [{"id": 4, "name": "report ünïcode.pdf", "size": 6,
                                         "content_type": "application/pdf", "inline": False}]
    assert thread[1]["attachments"] == []
