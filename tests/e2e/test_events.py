"""S5 + Phase 3 against the real stack: who hears what over the WebSocket.

An administrator and two agents (Support, Sales) are connected at once while osTicket changes. Each client must
hear exactly the events it is allowed to, once, and an agent who is disabled must be disconnected.
"""

import json
import threading
import time

import httpx
import pytest
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from tests.e2e.conftest import BASE_URL, DELIVERABLE_DOMAIN, login_as

pytestmark = pytest.mark.e2e

PASSWORD = "E2e-Pass-9x!Qz"
SUPPORT, SALES = 1, 2
WS_URL = BASE_URL.replace("http", "ws", 1) + "/api/v1/ws"


class Listener:
    """A WebSocket client that collects every message it is sent, in a thread."""

    def __init__(self, headers: dict):
        token = headers["Authorization"].split(" ", 1)[1]
        self.messages: list[dict] = []
        self.close_code: int | None = None
        self._ws = connect(WS_URL, subprotocols=["bearer", token], open_timeout=15)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            for raw in self._ws:
                self.messages.append(json.loads(raw))
        except ConnectionClosed as closed:
            self.close_code = closed.rcvd.code if closed.rcvd else 1006

    def events(self, aggregate_type=None, aggregate_id=None):
        return [
            m["type"] for m in self.messages
            if (aggregate_type is None or m["aggregate_type"] == aggregate_type)
            and (aggregate_id is None or str(m["aggregate_id"]) == str(aggregate_id))
        ]

    def wait_for(self, predicate, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate(self):
                return True
            time.sleep(0.2)
        return False

    def close(self):
        try:
            self._ws.close()
        except Exception:
            pass


def _agent(bridge, uniq, who, dept):
    username = f"e2e{who}{uniq}"
    sid = bridge.create(
        "/api/v1/staff",
        {"username": username, "firstname": "E2E", "lastname": who.title(), "email": f"{username}@{DELIVERABLE_DOMAIN}",
         "dept_id": dept, "role_id": 3, "password": PASSWORD, "is_active": True, "is_admin": False},
        cleanup="/api/v1/staff/{id}",
    )
    return sid, username


def test_each_client_hears_only_what_it_may_and_a_disabled_agent_is_disconnected(bridge, uniq):
    support_id, support_user = _agent(bridge, uniq, "es", SUPPORT)
    sales_id, sales_user = _agent(bridge, uniq, "ev", SALES)
    admin_headers = bridge.client.headers
    listeners = {
        "admin": Listener(admin_headers),
        "support": Listener(login_as(support_user, PASSWORD)),
        "sales": Listener(login_as(sales_user, PASSWORD)),
    }
    try:
        time.sleep(3)      # the API's event cursor is at the end of the log; anything after this is news

        # ── a ticket in Support: Support and the admin hear it, Sales does not ────────────────────────
        customer = bridge.create("/api/v1/users", {"email": f"ev.{uniq}@{DELIVERABLE_DOMAIN}", "name": "E2E Customer"},
                                 cleanup="/api/v1/users/{id}")
        ticket = bridge.create(
            "/api/v1/tickets/",
            {"subject": f"e2e events {uniq}", "message": "hello", "email": f"ev.{uniq}@{DELIVERABLE_DOMAIN}",
             "name": "E2E Customer", "dept_id": SUPPORT, "topic_id": 1},
            cleanup="/api/v1/tickets/{id}",
        )
        assert listeners["support"].wait_for(lambda l: "ticket.created" in l.events("ticket", ticket))
        assert listeners["admin"].wait_for(lambda l: "ticket.created" in l.events("ticket", ticket))
        time.sleep(2)
        assert listeners["sales"].events("ticket", ticket) == []

        # ── moved to Sales: Sales now hears about it; Support, who can no longer see it, does not ─────
        bridge.command("POST", f"/api/v1/tickets/{ticket}/transfer", {"dept_id": SALES, "note": "move"})
        assert listeners["sales"].wait_for(lambda l: "ticket.transferred" in l.events("ticket", ticket))
        bridge.command("POST", f"/api/v1/tickets/{ticket}/note", {"title": "n", "message": "internal"})
        assert listeners["sales"].wait_for(lambda l: "ticket.note_added" in l.events("ticket", ticket))
        time.sleep(2)
        assert "ticket.transferred" not in listeners["support"].events("ticket", ticket)
        assert "ticket.note_added" not in listeners["support"].events("ticket", ticket)

        # ── exactly once: one action, one event (no model-signal duplicates), for every recipient ─────
        for name in ("admin", "sales"):
            seen = listeners[name].events("ticket", ticket)
            assert seen.count("ticket.transferred") == 1, (name, seen)
            assert seen.count("ticket.note_added") == 1, (name, seen)
            assert not [e for e in seen if e.endswith((".updated", ".edited"))], (name, seen)   # no duplicate signal events

        # ── directory and Admin Panel events: only those entitled to them ────────────────────────────
        assert listeners["admin"].wait_for(lambda l: "user.created" in l.events("user", customer))
        time.sleep(1)
        assert listeners["support"].events("user") == [] and listeners["sales"].events("user") == []   # no user.dir

        # ── knowledge base: every agent hears about it ───────────────────────────────────────────────
        category = bridge.create("/api/v1/kb/categories", {"name": f"e2e-events-{uniq}", "description": "d", "is_public": True},
                                 cleanup="/api/v1/kb/categories/{id}")
        for name in ("admin", "support", "sales"):
            assert listeners[name].wait_for(lambda l: "kb.category.created" in l.events()), name

        # ── an admin-only object (staff): only the administrator ─────────────────────────────────────
        assert listeners["admin"].wait_for(lambda l: "staff.created" in l.events("staff"))
        assert listeners["support"].events("staff") == [] and listeners["sales"].events("staff") == []

        # ── a disabled agent is disconnected (checked every WS_RECHECK_SECONDS) ───────────────────────
        assert listeners["sales"].close_code is None
        bridge.command("PATCH", f"/api/v1/staff/{sales_id}", {"is_active": False})
        assert listeners["sales"].wait_for(lambda l: l.close_code is not None, timeout=40), "still connected"
        assert listeners["sales"].close_code == 1008
        assert listeners["support"].close_code is None and listeners["admin"].close_code is None
    finally:
        for listener in listeners.values():
            listener.close()


def test_a_connection_without_a_valid_token_is_refused():
    for subprotocols in ([], ["bearer", "not-a-token"]):
        with pytest.raises(Exception):
            connect(WS_URL, subprotocols=subprotocols, open_timeout=10).recv(timeout=5)


def test_a_disabled_agent_cannot_open_a_connection(bridge, uniq):
    sid, username = _agent(bridge, uniq, "ed", SUPPORT)
    headers = login_as(username, PASSWORD)
    bridge.command("PATCH", f"/api/v1/staff/{sid}", {"is_active": False})
    with pytest.raises(Exception):
        connect(WS_URL, subprotocols=["bearer", headers["Authorization"].split(" ", 1)[1]], open_timeout=10).recv(timeout=5)
