"""Fixtures and helpers shared by the live end-to-end tests (see test_bridge_*.py).

These need the real stack (``make start``) and are deselected by default (``-m "not e2e"``
in pyproject.toml). Run them with:

    set -a; . ./.env; set +a
    make test-e2e

Login uses OSTICKET_ADMIN_USERNAME / OSTICKET_ADMIN_PASSWORD from the environment, so the
tests never read a secret from disk.

Writes are asynchronous. The osticket-worker container drains the inbox about every second;
osTicket's cron (about once a minute) is the fallback. The tests wait server-side with
``GET /commands/{id}?wait=`` and only if a command is still pending after that do they run
cron themselves (E2E_CRON_CMD, set by ``make test-e2e``) — so a dead worker shows up as
slow tests, and test_commands_run_within_seconds_without_cron fails outright.
"""

import json
import os
import subprocess
import time
import uuid

import httpx
import pytest

BASE_URL = os.environ.get("E2E_BASE_URL", "http://localhost:8000")
USERNAME = os.environ.get("OSTICKET_ADMIN_USERNAME", "")
PASSWORD = os.environ.get("OSTICKET_ADMIN_PASSWORD", "")
CRON_CMD = os.environ.get("E2E_CRON_CMD", "")
POLL_TIMEOUT_S = 150
SERVER_WAIT_S = 20   # GET /commands/{id}?wait=… holds the request this long

# osTicket verifies that an email's domain has MX records, so reserved test domains
# (example.test, ...) are rejected by its user and staff forms.
DELIVERABLE_DOMAIN = "gmail.com"


class Bridge:
    """A logged-in API client that can queue a command and wait for the PHP bridge."""

    def __init__(self, client: httpx.Client):
        self.client = client
        self._cleanups: list[tuple[str, str]] = []

    # ── reading ──────────────────────────────────────────────────────────────

    def get(self, path: str, **kwargs) -> httpx.Response:
        return self.client.get(path, **kwargs)

    def read(self, path: str) -> dict:
        resp = self.client.get(path)
        assert resp.status_code == 200, f"GET {path} -> {resp.status_code}: {resp.text[:300]}"
        return resp.json()

    # ── writing ──────────────────────────────────────────────────────────────

    def _kick_cron(self) -> None:
        if CRON_CMD:
            subprocess.run(CRON_CMD, shell=True, capture_output=True, timeout=120)

    def _wait(self, command_id: int) -> dict:
        deadline = time.monotonic() + POLL_TIMEOUT_S
        detail: dict = {}
        while time.monotonic() < deadline:
            resp = self.client.get(f"/api/v1/commands/{command_id}", params={"wait": SERVER_WAIT_S})
            assert resp.status_code == 200, resp.text
            detail = resp.json()
            if detail["status"] not in ("pending", "processing"):
                return detail
            if detail["status"] == "pending":
                self._kick_cron()
        pytest.fail(
            f"command {command_id} still '{detail.get('status')}' after {POLL_TIMEOUT_S}s — "
            "is the osticket-cron container running, and is the plugin enabled?"
        )

    def send(self, method: str, path: str, json_body: dict | None = None) -> dict:
        """Queue a write, wait for the bridge, and return the finished command row."""
        resp = self.client.request(
            method, path, json=json_body, headers={"Idempotency-Key": uuid.uuid4().hex}
        )
        assert resp.status_code == 202, f"{method} {path} -> {resp.status_code}: {resp.text[:300]}"
        return self._wait(resp.json()["command_id"])

    def command(self, method: str, path: str, json_body: dict | None = None) -> dict:
        """Like send(), but the bridge must report success. Adds ``result`` (parsed) to the row."""
        done = self.send(method, path, json_body)
        assert done["status"] == "done", (
            f"{method} {path}: bridge reported {done['status']}: {done.get('error_message')}"
        )
        done["result"] = json.loads(done["result_payload"] or "{}")
        return done

    def create(self, path: str, body: dict, *, cleanup: str | None = None) -> int:
        """POST a create command and return the new resource's id.

        ``cleanup`` is a path template containing ``{id}``; if given, the resource is deleted
        when the test finishes (even when an assertion failed).
        """
        done = self.command("POST", path, body)
        new_id = next(
            (v for v in done["result"].values() if isinstance(v, int) and not isinstance(v, bool) and v > 0),
            None,
        )
        assert new_id, f"create {path} returned no id: {done['result_payload']}"
        if cleanup:
            self._cleanups.append(("DELETE", cleanup.format(id=new_id)))
        return new_id

    def refuses(self, method: str, path: str, body: dict | None, *, because: str) -> dict:
        """The bridge must FAIL this command, and its error must mention ``because``."""
        done = self.send(method, path, body)
        assert done["status"] == "failed", f"{method} {path} should have been refused, got {done['status']}"
        assert because.lower() in (done["error_message"] or "").lower(), done["error_message"]
        return done

    def cleanup(self) -> None:
        # newest first, so dependants (e.g. a ticket) go before what they depend on
        for method, path in reversed(self._cleanups):
            try:
                self.send(method, path)
            except Exception:  # best effort: never mask the real test failure
                pass
        self._cleanups.clear()


@pytest.fixture(scope="session")
def _client():
    if not (USERNAME and PASSWORD):
        pytest.skip("set OSTICKET_ADMIN_USERNAME and OSTICKET_ADMIN_PASSWORD (source .env)")
    with httpx.Client(base_url=BASE_URL, timeout=30) as client:
        try:
            client.get("/health").raise_for_status()
        except httpx.HTTPError as exc:
            pytest.skip(f"stack not reachable at {BASE_URL}: {exc}")
        login = client.post("/api/v1/auth/login", data={"username": USERNAME, "password": PASSWORD})
        assert login.status_code == 200, "admin login through the bridge auth endpoint failed"
        client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
        yield client


@pytest.fixture
def bridge(_client):
    b = Bridge(_client)
    yield b
    b.cleanup()


@pytest.fixture
def uniq():
    """A short unique suffix so repeated runs never collide on osTicket's unique names."""
    return uuid.uuid4().hex[:8]
