"""The /ws endpoint through Starlette's test client: who is let in, and how they are registered."""

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.auth import create_access_token
from app.core.ws_manager import manager
from app.main import app
from tests.conftest import make_access

IDENTITY = {"staff_id": 5, "username": "agent", "name": "Agent", "email": "a@x.test", "permissions": ["user.dir"]}


@pytest.fixture(autouse=True)
def _no_leftover_clients():
    yield
    for client in manager.clients():
        manager.disconnect(client)


def _connect(client, token=None, protocols=None):
    protocols = protocols if protocols is not None else (["bearer", token] if token else [])
    return client.websocket_connect("/api/v1/ws", subprotocols=protocols)


def _token(minutes=5, identity=IDENTITY):
    return create_access_token(identity, timedelta(minutes=minutes))


def test_a_connection_without_a_token_is_refused():
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as refused:
        with _connect(client):
            pass
    assert refused.value.code == 1008


def test_a_garbage_token_is_refused():
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as refused:
        with _connect(client, "not-a-token"):
            pass
    assert refused.value.code == 1008


def test_an_expired_token_is_refused():
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as refused:
        with _connect(client, _token(minutes=-5)):
            pass
    assert refused.value.code == 1008


@pytest.mark.parametrize("state", ["disabled", "deleted"])
def test_a_valid_token_for_a_disabled_or_deleted_agent_is_refused(state):
    client = TestClient(app)
    access = make_access(5, [], is_active=False) if state == "disabled" else None
    with patch("app.core.auth.load_access_context", new=AsyncMock(return_value=access)):
        with pytest.raises(WebSocketDisconnect) as refused:
            with _connect(client, _token()):
                pass
    assert refused.value.code == 1008


def test_a_valid_connection_is_registered_with_the_agents_live_access_and_removed_on_leaving():
    client = TestClient(app)
    with _connect(client, _token()) as ws:
        (registered,) = manager.clients()
        assert registered.staff_id == 5
        assert registered.access.has_global("user.dir")           # the AccessContext, not token claims
        assert registered.expires_at > 0
        ws.close()
    assert manager.client_count == 0


def test_the_bearer_subprotocol_is_negotiated():
    client = TestClient(app)
    with _connect(client, _token()) as ws:
        assert ws.accepted_subprotocol == "bearer"


def test_an_agent_over_the_connection_limit_is_refused(monkeypatch):
    monkeypatch.setattr(manager, "max_per_staff", 2)
    client = TestClient(app)
    with _connect(client, _token()), _connect(client, _token()):
        assert manager.client_count == 2
        with pytest.raises(WebSocketDisconnect):
            with _connect(client, _token()):
                pass
        assert manager.client_count == 2
