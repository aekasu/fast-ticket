"""Live end-to-end check of the core write path: FastAPI -> inbox -> PHP bridge -> osTicket -> read-back.

The smallest smoke test of the whole chain (canned responses); the per-domain coverage is in
test_bridge_domains.py. Needs the running stack — see conftest.py.
"""

import httpx
import pytest

from tests.e2e.conftest import BASE_URL, PASSWORD, USERNAME

pytestmark = pytest.mark.e2e


def test_bad_credentials_are_rejected(bridge):
    resp = httpx.post(f"{BASE_URL}/api/v1/auth/login", data={"username": USERNAME, "password": "definitely-wrong"})
    assert resp.status_code == 401


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
