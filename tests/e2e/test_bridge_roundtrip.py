"""Live end-to-end check of the write path: FastAPI -> inbox -> PHP bridge -> osTicket -> read-back.

Needs the real stack (``make start``), so it is deselected by default (see ``addopts`` in
pyproject.toml). Run it with:

    set -a; . ./.env; set +a
    make test-e2e

It logs in with OSTICKET_ADMIN_USERNAME / OSTICKET_ADMIN_PASSWORD, so no secret is read
from disk by the tests themselves. Everything it creates is deleted again.

Writes are asynchronous: the osTicket cron container drains the inbox about once a
minute, so each step polls the command until the bridge has processed it.
"""

import json
import os
import time
import uuid

import httpx
import pytest

pytestmark = pytest.mark.e2e

BASE_URL = os.environ.get("E2E_BASE_URL", "http://localhost:8000")
USERNAME = os.environ.get("OSTICKET_ADMIN_USERNAME", "")
PASSWORD = os.environ.get("OSTICKET_ADMIN_PASSWORD", "")
POLL_TIMEOUT_S = 150
POLL_INTERVAL_S = 3


@pytest.fixture(scope="module")
def api():
    if not (USERNAME and PASSWORD):
        pytest.skip("set OSTICKET_ADMIN_USERNAME and OSTICKET_ADMIN_PASSWORD (source .env)")
    with httpx.Client(base_url=BASE_URL, timeout=15) as client:
        try:
            client.get("/health").raise_for_status()
        except httpx.HTTPError as exc:
            pytest.skip(f"stack not reachable at {BASE_URL}: {exc}")
        login = client.post("/api/v1/auth/login", data={"username": USERNAME, "password": PASSWORD})
        assert login.status_code == 200, "admin login through the bridge auth endpoint failed"
        client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
        yield client


def _wait_for_bridge(api: httpx.Client, command_id: int) -> dict:
    """Poll the inbox row until the PHP bridge has finished with it."""
    deadline = time.monotonic() + POLL_TIMEOUT_S
    detail: dict = {}
    while time.monotonic() < deadline:
        resp = api.get(f"/api/v1/commands/{command_id}")
        assert resp.status_code == 200, resp.text
        detail = resp.json()
        if detail["status"] not in ("pending", "processing"):
            return detail
        time.sleep(POLL_INTERVAL_S)
    pytest.fail(
        f"command {command_id} still '{detail.get('status')}' after {POLL_TIMEOUT_S}s — "
        "is the osticket-cron container running, and is the plugin enabled?"
    )


def _queue(api: httpx.Client, method: str, url: str, **kwargs) -> dict:
    resp = api.request(method, url, headers={"Idempotency-Key": uuid.uuid4().hex}, **kwargs)
    assert resp.status_code == 202, resp.text
    done = _wait_for_bridge(api, resp.json()["command_id"])
    assert done["status"] == "done", f"bridge reported {done['status']}: {done.get('error_message')}"
    return done


def test_bad_credentials_are_rejected(api):
    resp = httpx.post(f"{BASE_URL}/api/v1/auth/login", data={"username": USERNAME, "password": "definitely-wrong"})
    assert resp.status_code == 401


def test_canned_response_full_lifecycle_through_the_bridge(api):
    title = f"e2e {uuid.uuid4().hex[:8]}"
    canned_id = None
    try:
        # create — disabled, to prove a falsy is_enabled survives osTicket's ORM
        created = _queue(
            api, "POST", "/api/v1/canned",
            json={"title": title, "response": "created by the e2e test", "is_enabled": False, "notes": "e2e"},
        )
        assert created["command_type"] == "kb.canned.create"
        canned_id = int(json.loads(created["result_payload"])["canned_id"])
        assert canned_id > 0, "handler reported done but no canned_id — it did not persist anything"

        # read back through the read path (direct SQL against osTicket's tables)
        got = api.get(f"/api/v1/canned/{canned_id}")
        assert got.status_code == 200, got.text
        body = got.json()
        assert body["title"] == title
        assert body["response"] == "created by the e2e test"
        assert body["is_enabled"] is False

        # partial update — only the title; the other fields must be preserved
        _queue(api, "PATCH", f"/api/v1/canned/{canned_id}", json={"title": title + " (renamed)"})
        body = api.get(f"/api/v1/canned/{canned_id}").json()
        assert body["title"] == title + " (renamed)"
        assert body["response"] == "created by the e2e test"
        assert body["is_enabled"] is False

        # delete
        _queue(api, "DELETE", f"/api/v1/canned/{canned_id}")
        assert api.get(f"/api/v1/canned/{canned_id}").status_code == 404
        canned_id = None
    finally:
        if canned_id is not None:  # leave the dev DB clean if an assertion above failed
            api.request("DELETE", f"/api/v1/canned/{canned_id}", headers={"Idempotency-Key": uuid.uuid4().hex})
