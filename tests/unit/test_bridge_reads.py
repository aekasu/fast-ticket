"""The read registry and the API's calls to it agree; the bridge client maps refusals the way the API promises."""

import re
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException

from app.integration.osticket import bridge_read

ROOT = Path(__file__).resolve().parents[2]


def registry_ops() -> set[str]:
    return set(re.findall(r"^\s*'([a-z_.]+)'\s*=>\s*\[", (ROOT / "bridge" / "reads.php").read_text(), re.M))


def called_ops() -> set[str]:
    found = set()
    for path in (ROOT / "app").rglob("*.py"):
        found |= set(re.findall(r"""(?:bridge_json|open_bridge_stream|bridge_download)\(\s*["']([a-z_.]+)["']""", path.read_text()))
    return found


def test_every_registered_read_is_used_and_every_call_is_registered():
    assert registry_ops() == called_ops() == {"canned.render", "file.stream", "system.info", "dashboard.report", "filter.options", "schedule.describe", "settings.options", "ticket.print", "ticket.zip", "task.print"}


def test_every_read_handler_exists_in_the_php():
    reads = (ROOT / "bridge" / "reads.php").read_text()
    handlers = (ROOT / "bridge" / "handlers" / "reads.php").read_text()
    for method in re.findall(r"'FastApiReadHandler',\s*'(\w+)'", reads):
        assert re.search(rf"public static function {method}\(", handlers), method


def test_the_read_url_is_the_auth_url_with_the_read_path():
    assert bridge_read.read_url("http://osticket/api/auth/integration") == "http://osticket/api/fast-ticket/read"
    assert bridge_read.read_url("http://osticket/api/auth/integration/") == "http://osticket/api/fast-ticket/read"


@pytest.mark.parametrize("status,body,expected", [
    (404, {"code": "not_found", "error": "No such ticket."}, 404),
    (403, {"code": "forbidden", "error": "no"}, 404),                 # never a 403 for what the agent may not open
    (501, {"code": "file_unavailable", "error": "backend"}, 501),
    (403, {"code": "invalid_integration_key", "error": "key"}, 502),   # our configuration, not the client's problem
    (500, {"code": "error", "error": "boom"}, 502),
    (400, {}, 502),
])
def test_refusals_are_mapped(status, body, expected):
    err = bridge_read.refusal(status, body)
    assert isinstance(err, HTTPException) and err.status_code == expected
    if expected == 502:
        assert "boom" not in err.detail and "key" not in err.detail      # internals are not echoed


@pytest.mark.asyncio
async def test_json_reads_return_the_result_and_map_errors(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"], seen["key"] = request.content, request.headers.get("x-integration-key")
        if b'"op":"missing"' in request.content.replace(b" ", b""):
            return httpx.Response(404, json={"code": "not_found", "error": "No such ticket."})
        return httpx.Response(200, json={"result": {"ok": True}})

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient
    monkeypatch.setattr(bridge_read.httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))
    assert await bridge_read.bridge_json("canned.render", 7, {"canned_id": 1}) == {"ok": True}
    assert seen["key"] and b'"staff_id": 7' in seen["body"].replace(b'":7', b'": 7')
    with pytest.raises(HTTPException) as gone:
        await bridge_read.bridge_json("missing", 7, {})
    assert gone.value.status_code == 404
