"""The bridge's read mode against the live stack: osTicket's own PHP answers what SQL cannot."""

import os
from pathlib import Path

import httpx
import pytest

from tests.e2e.conftest import BASE_URL, DELIVERABLE_DOMAIN

pytestmark = pytest.mark.e2e
OSTICKET_READ = os.environ.get("E2E_OSTICKET_READ_URL", "http://localhost:8080/api/fast-ticket/read")


def _ask(op, staff_id, params, key=None):
    key = os.environ.get("FASTAPI_INTEGRATION_KEY") if key is None else key
    if not key:
        pytest.skip("needs FASTAPI_INTEGRATION_KEY (source .env) to call the read endpoint directly")
    return httpx.post(OSTICKET_READ, json={"op": op, "staff_id": staff_id, "params": params},
                      headers={"X-Integration-Key": key}, timeout=30)


def _ticket(bridge, uniq, suffix=""):
    email = f"rd{suffix}.{uniq}@{DELIVERABLE_DOMAIN}"
    tk = bridge.create("/api/v1/tickets/", {"subject": f"e2e read {uniq}{suffix}", "message": "m", "email": email,
                                            "name": "Ada Lovelace", "dept_id": 1, "topic_id": 1})
    holder = bridge.read(f"/api/v1/tickets/{tk}")
    bridge._cleanups.append(("DELETE", f"/api/v1/users/{holder['user_id']}"))
    bridge._cleanups.append(("DELETE", f"/api/v1/tickets/{tk}"))
    return tk, holder


def test_a_canned_response_comes_back_with_the_tickets_variables_filled_in(bridge, uniq):
    tk, holder = _ticket(bridge, uniq)
    canned = bridge.create("/api/v1/canned", {
        "title": f"e2e-render-{uniq}", "dept_id": 0,
        "response": "<p>Hello %{ticket.name.first}, about %{ticket.subject} (#%{ticket.number}) %{nonsense.var}</p>"},
        cleanup="/api/v1/canned/{id}")
    got = bridge.read(f"/api/v1/tickets/{tk}/canned/{canned}")
    assert got["id"] == canned and got["title"] == f"e2e-render-{uniq}" and got["format"] == "html"
    assert f"Hello Ada, about e2e read {uniq} (#{holder['number']})" in got["response"]      # osTicket's own expansion
    assert "%{ticket.name.first}" not in got["response"] and got["files"] == []
    plain = bridge.read(f"/api/v1/tickets/{tk}/canned/{canned}?plain=true")
    assert plain["format"] == "text" and "<p>" not in plain["response"] and f"#{holder['number']}" in plain["response"]

    assert bridge.get(f"/api/v1/tickets/{tk}/canned/999999").status_code == 404
    assert bridge.get(f"/api/v1/tickets/999999/canned/{canned}").status_code == 404
    disabled = bridge.create("/api/v1/canned", {"title": f"e2e-off-{uniq}", "response": "x", "is_enabled": False, "dept_id": 0},
                             cleanup="/api/v1/canned/{id}")
    assert bridge.get(f"/api/v1/tickets/{tk}/canned/{disabled}").status_code == 404        # osTicket: a disabled reply is gone


def test_the_read_endpoint_checks_the_key_the_staff_member_and_the_operation(bridge, uniq):
    tk, _ = _ticket(bridge, uniq, "k")
    ok = _ask("canned.render", 1, {"ticket_id": tk, "canned_id": 0})
    assert ok.status_code == 404 and ok.json()["code"] == "not_found"                      # reached the handler
    assert _ask("canned.render", 1, {}, key="not-the-key-not-the-key-not-the-key-12").status_code == 403
    assert _ask("canned.render", 999999, {"ticket_id": tk, "canned_id": 1}).status_code == 403    # no such staff member
    bad_op = _ask("rm.rf", 1, {})
    assert bad_op.status_code == 400 and bad_op.json()["code"] == "bad_request"
    assert httpx.get(OSTICKET_READ, headers={"X-Integration-Key": "x"}).status_code != 200   # POST only


def test_file_stream_reads_the_bytes_through_osticket_and_refuses_what_is_not_attached(bridge, uniq):
    from tests.e2e.test_attachments import _b64, _sweep_uploaded, sql   # reuse the upload and SQL helpers
    if not os.environ.get("MYSQL_PASSWORD"):
        pytest.skip("needs MYSQL_PASSWORD")
    _sweep_uploaded(bridge, f"e2e-rd-{uniq}%")
    tk, _ = _ticket(bridge, uniq, "f")
    other, _ = _ticket(bridge, uniq, "g")
    payload = bytes(range(256)) * 40
    done = bridge.command("POST", f"/api/v1/tickets/{tk}/reply", {"message": "file", "attachments": [
        {"name": f"e2e-rd-{uniq}.bin", "data": _b64(payload)}]})
    attachment = next(e for e in bridge.read(f"/api/v1/tickets/{tk}")["thread"]
                      if e["id"] == done["result"]["entry_id"])["attachments"][0]["id"]

    got = _ask("file.stream", 1, {"object_type": "T", "object_id": tk, "attachment_id": attachment})
    assert got.status_code == 200 and got.content == payload and got.headers["content-length"] == str(len(payload))
    # not this ticket's, not a task's, not there: the same 404 whatever is wrong
    for params in ({"object_type": "T", "object_id": other, "attachment_id": attachment},
                   {"object_type": "A", "object_id": tk, "attachment_id": attachment},
                   {"object_type": "T", "object_id": tk, "attachment_id": 99999999},
                   {"object_type": "X", "object_id": tk, "attachment_id": attachment}):
        refused = _ask("file.stream", 1, params)
        assert refused.status_code == 404 and refused.json()["code"] == "not_found", params
