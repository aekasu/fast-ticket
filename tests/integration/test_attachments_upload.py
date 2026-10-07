"""Files travel in the command payload as size-capped base64."""

import base64
import json
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import select

from app.core.config import settings
from app.core.redaction import redact_json
from app.integration.osticket.commands import IntegrationInbox
from app.main import app
from tests.conftest import TestingSessionLocal
from tests.integration.test_tasks import _auth

b64 = lambda raw: base64.b64encode(raw).decode()      # noqa: E731
FILE = {"name": "report.pdf", "content_type": "application/pdf", "data": b64(b"%PDF-1.4 hello")}


async def _rows():
    async with TestingSessionLocal() as s:
        return (await s.execute(select(IntegrationInbox).order_by(IntegrationInbox.id))).scalars().all()


@pytest.mark.asyncio
@pytest.mark.parametrize("url,perm,body_key", [
    ("/api/v1/tickets/5/reply", "ticket.reply", "message"), ("/api/v1/tickets/5/note", "ticket.reply", "message"),
    ("/api/v1/tasks/5/reply", "task.reply", "message"), ("/api/v1/tasks/5/note", "task.reply", "note"),
])
async def test_a_reply_or_note_carries_files_into_the_payload(url, perm, body_key):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        ok = await ac.post(url, json={body_key: "see attached", "attachments": [FILE]}, headers=_auth([perm]))
        plain = await ac.post(url, json={body_key: "no files"}, headers=_auth([perm]))
    assert (ok.status_code, plain.status_code) == (202, 202)
    first, second = await _rows()
    sent = json.loads(first.payload)["attachments"]
    assert sent == [{"name": "report.pdf", "type": "application/pdf", "file_data": FILE["data"]}]
    assert "attachments" not in json.loads(second.payload)              # nothing added when nothing is attached
    assert "[redacted]" in redact_json(first.payload) and FILE["data"] not in redact_json(first.payload)   # never shown back


@pytest.mark.asyncio
async def test_bad_files_are_refused_before_anything_is_queued(monkeypatch):
    monkeypatch.setattr(settings, "ATTACHMENT_MAX_FILE_BYTES", 1000)
    monkeypatch.setattr(settings, "ATTACHMENT_MAX_FILES", 2)
    monkeypatch.setattr(settings, "ATTACHMENT_MAX_TOTAL_BYTES", 1500)
    url, headers = "/api/v1/tickets/5/reply", _auth(["ticket.reply"])
    small = {"name": "a.txt", "data": b64(b"x" * 800)}
    cases = {
        "not base64": [{"name": "a.txt", "data": "***not base64***"}],
        "a path in the name": [{"name": "../etc/passwd", "data": b64(b"x")}],
        "an empty name": [{"name": "", "data": b64(b"x")}],
        "a bad media type": [{"name": "a.txt", "content_type": "not a type", "data": b64(b"x")}],
        "too big a file": [{"name": "big.bin", "data": b64(b"x" * 1001)}],
        "too many files": [small, small, small],
        "too many bytes together": [small, {"name": "b.txt", "data": b64(b"y" * 800)}],
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        codes = {label: (await ac.post(url, json={"message": "m", "attachments": files}, headers=headers)).status_code
                 for label, files in cases.items()}
        ok = await ac.post(url, json={"message": "m", "attachments": [small]}, headers=headers)
    assert set(codes.values()) == {422}, codes
    assert ok.status_code == 202 and len(await _rows()) == 1


@pytest.mark.asyncio
async def test_a_large_body_is_accepted_on_upload_routes_only(monkeypatch):
    big = {"message": "m", "attachments": [{"name": "big.bin", "data": b64(b"z" * 3_000_000)}]}   # ~4 MB of base64
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        reply = await ac.post("/api/v1/tickets/5/reply", json=big, headers=_auth(["ticket.reply"]))
        other = await ac.post("/api/v1/tickets/5/close", json={"note": "x" * 2_000_000}, headers=_auth(["ticket.close"]))
    assert reply.status_code == 202            # over MAX_REQUEST_BYTES (1 MB), under the upload limit
    assert other.status_code == 413            # every other route keeps the small limit


@pytest.mark.asyncio
async def test_attachment_limits_are_open_to_any_agent():
    with patch("app.modules.settings.service.repo.get_max_file_size", new=AsyncMock(return_value=1048576)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            ok = await ac.get("/api/v1/settings/attachments", headers=_auth([]))
            anon = await ac.get("/api/v1/settings/attachments")
    assert (ok.status_code, anon.status_code) == (200, 401)
    body = ok.json()
    assert body["max_file_bytes"] == min(1048576, settings.ATTACHMENT_MAX_FILE_BYTES)
    assert (body["osticket_max_file_bytes"], body["max_files"]) == (1048576, settings.ATTACHMENT_MAX_FILES)


@pytest.mark.asyncio
async def test_a_new_ticket_carries_files_on_its_first_message():
    body = {"subject": "s", "message": "see attached", "email": "a@b.example", "name": "A", "attachments": [FILE]}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        ok = await ac.post("/api/v1/tickets/", json=body, headers=_auth(["ticket.create"]))
    assert ok.status_code == 202
    (row,) = await _rows()
    payload = json.loads(row.payload)
    assert payload["attachments"] == [{"name": "report.pdf", "type": "application/pdf", "file_data": FILE["data"]}]
    assert "phone" not in payload and "user_id" not in payload        # unset fields are still left out
