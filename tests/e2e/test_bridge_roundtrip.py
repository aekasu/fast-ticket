"""Live end-to-end check of the core write path: FastAPI -> inbox -> PHP bridge -> osTicket -> read-back.

The smallest smoke test of the whole chain (canned responses); the per-domain coverage is in
test_bridge_domains.py. Needs the running stack — see conftest.py.
"""

import json
import time
import uuid

import httpx
import pytest

from tests.e2e.conftest import BASE_URL, DELIVERABLE_DOMAIN, PASSWORD, USERNAME

pytestmark = pytest.mark.e2e


def test_bad_credentials_are_rejected(bridge):
    resp = httpx.post(f"{BASE_URL}/api/v1/auth/login", data={"username": USERNAME, "password": "definitely-wrong"})
    assert resp.status_code == 401
    assert resp.headers["x-login-error"] == "invalid_credentials"


def test_good_credentials_are_accepted(bridge):
    resp = httpx.post(f"{BASE_URL}/api/v1/auth/login", data={"username": USERNAME, "password": PASSWORD})
    assert resp.status_code == 200 and resp.json()["access_token"]


def test_canned_response_full_lifecycle_through_the_bridge(bridge, uniq):
    title = f"e2e {uniq}"
    # create — disabled, to prove a falsy is_enabled survives osTicket's ORM
    canned_id = bridge.create(
        "/api/v1/canned",
        {"title": title, "response": "created by the e2e test", "is_enabled": False, "notes": "e2e"},
        cleanup="/api/v1/canned/{id}",
    )
    body = bridge.read(f"/api/v1/canned/{canned_id}")
    assert (body["title"], body["response"], body["is_enabled"]) == (title, "created by the e2e test", False)

    # partial update — only the title; the other fields must be preserved
    bridge.command("PATCH", f"/api/v1/canned/{canned_id}", {"title": title + " (renamed)"})
    body = bridge.read(f"/api/v1/canned/{canned_id}")
    assert (body["title"], body["response"], body["is_enabled"]) == (title + " (renamed)", "created by the e2e test", False)

    bridge.command("DELETE", f"/api/v1/canned/{canned_id}")
    assert bridge.get(f"/api/v1/canned/{canned_id}").status_code == 404


def test_commands_run_within_seconds_without_cron(bridge, uniq):
    """The inbox worker, not the once-a-minute cron, executes commands (no cron kick here)."""
    started = time.monotonic()
    resp = bridge.client.post(
        "/api/v1/canned",
        json={"title": f"e2e latency {uniq}", "response": "latency probe", "is_enabled": False},
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )
    assert resp.status_code == 202
    command_id = resp.json()["command_id"]
    detail = bridge.client.get(f"/api/v1/commands/{command_id}", params={"wait": 15}).json()
    elapsed = time.monotonic() - started

    assert detail["status"] == "done", detail
    assert elapsed < 10, f"took {elapsed:.1f}s — is the osticket-worker container running?"
    canned_id = next(v for v in json.loads(detail["result_payload"]).values() if isinstance(v, int))
    bridge.command("DELETE", f"/api/v1/canned/{canned_id}")


def test_a_disabled_agents_token_stops_working_at_once(bridge, uniq):
    """Deactivating an agent in osTicket cuts off a token that is still validly signed and unexpired."""
    username, password = f"e2erev{uniq}", "E2e-Pass-9x!Qz"
    sid = bridge.create(
        "/api/v1/staff",
        {"username": username, "firstname": "E2E", "lastname": "Revoke", "email": f"e2erev{uniq}@{DELIVERABLE_DOMAIN}",
         "dept_id": 1, "role_id": 3, "password": password, "is_active": True, "is_admin": False},
        cleanup="/api/v1/staff/{id}",
    )
    login = httpx.post(f"{BASE_URL}/api/v1/auth/login", data={"username": username, "password": password}, timeout=30)
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert httpx.get(f"{BASE_URL}/api/v1/auth/me", headers=headers, timeout=30).status_code == 200

    bridge.command("PATCH", f"/api/v1/staff/{sid}", {"is_active": False})

    revoked = httpx.get(f"{BASE_URL}/api/v1/auth/me", headers=headers, timeout=30)
    assert revoked.status_code == 401 and "no longer active" in revoked.json()["detail"]

    # and the disabled account cannot obtain a new one — with the reason spelled out
    again = httpx.post(f"{BASE_URL}/api/v1/auth/login", data={"username": username, "password": password}, timeout=30)
    assert again.status_code == 401 and again.headers["x-login-error"] == "account_disabled"
