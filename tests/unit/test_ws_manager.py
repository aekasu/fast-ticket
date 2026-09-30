"""ConnectionManager — delivery, limits, and keeping connections honest."""

import asyncio

import pytest

from app.core.ws_manager import CLOSE_POLICY, CLOSE_TRY_LATER, Client, ConnectionManager
from tests.conftest import make_access

FAR = 9e12


class FakeSocket:
    def __init__(self, *, stall=0.0, fail=False):
        self.accepted = False
        self.closed: tuple[int, str] | None = None
        self.sent: list[dict] = []
        self.stall, self.fail = stall, fail

    async def accept(self, subprotocol=None):
        self.accepted = True

    async def close(self, code=1000, reason=""):
        self.closed = (code, reason)

    async def send_json(self, message):
        if self.stall:
            await asyncio.sleep(self.stall)
        if self.fail:
            raise RuntimeError("broken pipe")
        self.sent.append(message)


async def _connect(manager, staff_id=1, expires_at=FAR, **socket):
    ws = FakeSocket(**socket)
    client = await manager.connect(ws, make_access(staff_id, []), expires_at)
    return ws, client


@pytest.mark.asyncio
async def test_connect_accepts_and_registers_the_client():
    manager = ConnectionManager()
    ws, client = await _connect(manager, staff_id=4)
    assert ws.accepted and client.staff_id == 4 and manager.client_count == 1


@pytest.mark.asyncio
async def test_an_agent_over_their_limit_is_refused_without_being_accepted():
    manager = ConnectionManager(max_per_staff=2)
    await _connect(manager, 1)
    await _connect(manager, 1)
    ws, client = await _connect(manager, 1)
    assert client is None and not ws.accepted and ws.closed[0] == CLOSE_TRY_LATER
    assert (await _connect(manager, 2))[1] is not None         # the limit is per agent
    assert manager.client_count == 3


@pytest.mark.asyncio
async def test_send_reaches_exactly_the_clients_given():
    manager = ConnectionManager()
    (a, ca), (b, cb), (c, cc) = [await _connect(manager, i) for i in (1, 2, 3)]
    assert await manager.send([ca, cc], {"type": "x"}) == 2
    assert a.sent == [{"type": "x"}] and c.sent == [{"type": "x"}] and b.sent == []


@pytest.mark.asyncio
async def test_a_client_whose_send_fails_is_dropped_and_the_rest_still_get_it():
    manager = ConnectionManager()
    bad, cbad = await _connect(manager, 1, fail=True)
    good, cgood = await _connect(manager, 2)
    assert await manager.send([cbad, cgood], {"type": "x"}) == 1
    assert good.sent and manager.clients() == (cgood,)


@pytest.mark.asyncio
async def test_a_client_that_stalls_is_dropped_instead_of_blocking_delivery():
    manager = ConnectionManager(send_timeout=0.05)
    slow, cslow = await _connect(manager, 1, stall=1.0)
    fast, cfast = await _connect(manager, 2)
    assert await manager.send([cslow, cfast], {"type": "x"}) == 1
    assert manager.clients() == (cfast,)


@pytest.mark.asyncio
async def test_an_expired_token_is_closed_at_send_time_not_delivered_to():
    manager = ConnectionManager()
    ws, client = await _connect(manager, 1, expires_at=1.0)
    assert await manager.send([client], {"type": "x"}) == 0
    assert ws.sent == [] and ws.closed[0] == CLOSE_POLICY and manager.client_count == 0


@pytest.mark.asyncio
async def test_disconnect_is_idempotent():
    manager = ConnectionManager()
    _, client = await _connect(manager)
    manager.disconnect(client)
    manager.disconnect(client)
    assert manager.client_count == 0


# ── revalidation ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_revalidate_closes_expired_disabled_and_deleted_and_keeps_the_rest():
    manager = ConnectionManager()
    expired, _ = await _connect(manager, 1, expires_at=100.0)
    disabled, _ = await _connect(manager, 2)
    deleted, _ = await _connect(manager, 3)
    healthy, healthy_client = await _connect(manager, 4)

    async def load(staff_id):
        return {2: make_access(2, [], is_active=False), 3: None, 4: make_access(4, ["user.dir"])}[staff_id]

    summary = await manager.revalidate(load, now=500.0)
    assert summary == {"expired": 1, "deactivated": 2, "refreshed": 1}
    assert expired.closed[0] == disabled.closed[0] == deleted.closed[0] == CLOSE_POLICY
    assert healthy.closed is None and manager.clients() == (healthy_client,)


@pytest.mark.asyncio
async def test_revalidate_refreshes_a_role_change_on_an_open_connection():
    manager = ConnectionManager()
    _, client = await _connect(manager, 5)
    assert not client.access.has_global("user.dir")

    async def load(staff_id):
        return make_access(5, ["user.dir"])

    await manager.revalidate(load, now=1.0)
    assert client.access.has_global("user.dir")               # the next event is judged by the new role


@pytest.mark.asyncio
async def test_revalidate_loads_each_agent_once_however_many_sockets_they_hold():
    manager = ConnectionManager()
    for _ in range(3):
        await _connect(manager, 9)
    loads = []

    async def load(staff_id):
        loads.append(staff_id)
        return make_access(staff_id, [])

    await manager.revalidate(load, now=1.0)
    assert loads == [9]
