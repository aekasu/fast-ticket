"""Live end-to-end check of the core write path: FastAPI -> inbox -> PHP bridge -> osTicket -> read-back.

The smallest smoke test of the whole chain (canned responses); the per-domain coverage is in
test_bridge_domains.py. Needs the running stack — see conftest.py.
"""

import json
import time
import uuid

import httpx
import pytest

from tests.e2e.conftest import BASE_URL, DELIVERABLE_DOMAIN, PASSWORD, USERNAME, login_raw

pytestmark = pytest.mark.e2e


def test_bad_credentials_are_rejected(bridge):
    resp = login_raw(USERNAME, "definitely-wrong")
    assert resp.status_code == 401
    assert resp.headers["x-login-error"] == "invalid_credentials"


def test_good_credentials_are_accepted(bridge):
    resp = login_raw(USERNAME, PASSWORD)
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
    login = login_raw(username, password)
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert httpx.get(f"{BASE_URL}/api/v1/auth/me", headers=headers, timeout=30).status_code == 200

    bridge.command("PATCH", f"/api/v1/staff/{sid}", {"is_active": False})

    revoked = httpx.get(f"{BASE_URL}/api/v1/auth/me", headers=headers, timeout=30)
    assert revoked.status_code == 401 and "no longer active" in revoked.json()["detail"]

    # and the disabled account cannot obtain a new one — with the reason spelled out
    again = login_raw(username, password)
    assert again.status_code == 401 and again.headers["x-login-error"] == "account_disabled"


def test_a_real_agents_access_follows_their_osticket_role_and_changes_at_once(bridge, uniq):
    """A non-admin agent gets exactly what osTicket gives their role — and a role change applies to the
    very next request on the same token (no re-login), because access is read on every request."""
    username, password = f"e2eaccess{uniq}", "E2e-Pass-9x!Qz"
    sid = bridge.create(
        "/api/v1/staff",
        {"username": username, "firstname": "E2E", "lastname": "Access", "email": f"e2eaccess{uniq}@{DELIVERABLE_DOMAIN}",
         "dept_id": 1, "role_id": 3, "password": password, "is_active": True, "is_admin": False},   # 3 = Limited Access
        cleanup="/api/v1/staff/{id}",
    )
    login = login_raw(username, password)
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    def status(method, path, **kwargs):
        return httpx.request(method, f"{BASE_URL}{path}", headers=headers, timeout=30, **kwargs).status_code

    def me():
        return httpx.get(f"{BASE_URL}/api/v1/auth/me", headers=headers, timeout=30).json()

    # what osTicket says about them
    now = me()
    assert now["is_admin"] is False and now["global_permissions"] == []
    assert "ticket.create" in now["roles"]["1"] and "task.create" not in now["roles"]["1"]
    assert now["department_ids"] == [1]

    # the Admin Panel and the user directory are closed to them
    # (the department *name list* is open to every agent, narrowed to their own department)
    departments = httpx.get(f"{BASE_URL}/api/v1/departments", headers=headers, timeout=30)
    assert departments.status_code == 200
    assert [d["id"] for d in departments.json()["items"]] == [1]
    # (the command list is open to every agent but shows only the commands they queued: none yet)
    own_commands = httpx.get(f"{BASE_URL}/api/v1/commands", headers=headers, timeout=30)
    assert own_commands.status_code == 200 and own_commands.json()["items"] == []
    for path in ("/api/v1/staff", "/api/v1/departments/1", "/api/v1/banlist", "/api/v1/slas", "/api/v1/users"):
        assert status("GET", path) == 403, path
    # search is open to any agent but scoped: no user.dir, so it can never return people
    searched = httpx.get(f"{BASE_URL}/api/v1/search?q=test", headers=headers, timeout=30)
    assert searched.status_code == 200
    assert {r["type"] for r in searched.json()["results"]} <= {"ticket"}
    # any agent may read the knowledge base, but writing it needs faq.manage
    assert status("GET", "/api/v1/kb/faqs") == 200
    assert status("POST", "/api/v1/kb/faqs", json={}) == 403
    # Limited Access can create tickets (gate passed -> the empty body is then a 422) but not tasks, and may read but not write canned responses
    assert status("POST", "/api/v1/tickets/", json={}) == 422
    assert status("POST", "/api/v1/tasks/", json={}) == 403
    assert status("GET", "/api/v1/canned") == 200                       # the reply picker is for every agent
    assert status("POST", "/api/v1/canned", json={}) == 403               # writing needs canned.manage

    # promote to All Access: the same token, the next request
    bridge.command("PATCH", f"/api/v1/staff/{sid}", {"role_id": 1})
    assert status("POST", "/api/v1/canned", json={}) == 422                    # canned.manage: the gate is passed
    assert status("POST", "/api/v1/tasks/", json={}) == 422
    assert "canned.manage" in me()["roles"]["1"]

    # demote to View only: nothing they could do a moment ago
    bridge.command("PATCH", f"/api/v1/staff/{sid}", {"role_id": 4})
    assert status("POST", "/api/v1/tickets/", json={}) == 403
    assert status("POST", "/api/v1/canned", json={}) == 403


def test_a_warm_access_cache_does_not_hide_a_department_move_or_a_disable(bridge, uniq):
    """The access cache is warm (several requests on one token), then the bridge moves the agent to another
    department and later disables them: each applies on the very next request, the way it did before the cache."""
    username, password = f"e2ecache{uniq}", "E2e-Pass-9x!Qz"
    sid = bridge.create(
        "/api/v1/staff",
        {"username": username, "firstname": "E2E", "lastname": "Cache", "email": f"e2ecache{uniq}@{DELIVERABLE_DOMAIN}",
         "dept_id": 1, "role_id": 3, "password": password, "is_active": True, "is_admin": False},
        cleanup="/api/v1/staff/{id}",
    )
    login = login_raw(username, password)
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    def me():
        return httpx.get(f"{BASE_URL}/api/v1/auth/me", headers=headers, timeout=30)

    for _ in range(3):                                   # warm: the second and third reuse the cached grants
        assert me().json()["department_ids"] == [1]

    bridge.command("PATCH", f"/api/v1/staff/{sid}", {"dept_id": 2})
    assert me().json()["department_ids"] == [2]

    bridge.command("PATCH", f"/api/v1/staff/{sid}", {"is_active": False})
    assert me().status_code == 401
