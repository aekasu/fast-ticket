"""WebSocket connections and who each one may hear about.

Every connection is a ``Client``: the socket, the staff member's ``AccessContext`` (what osTicket lets them see
right now) and when their token expires. Delivery is decided per client by ``app.modules.events.visibility`` —
a broadcast is never "everyone connected".

Connections do not outlive their authority: ``revalidate`` (run every ``WS_RECHECK_SECONDS``) closes a client
whose token has expired or whose account was disabled or deleted, and refreshes the access of the rest, so a
role change reaches an open connection within seconds.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field

from fastapi import WebSocket

from app.core.access import AccessContext

logger = logging.getLogger(__name__)

# WebSocket close codes: 1008 = policy violation (auth), 1013 = try again later (over the limit).
CLOSE_POLICY = 1008
CLOSE_TRY_LATER = 1013


@dataclass(eq=False)
class Client:
    ws: WebSocket
    access: AccessContext
    expires_at: float          # the token's ``exp`` (epoch seconds)
    issued_at: float = 0       # the token's ``iat``
    connected_at: float = field(default_factory=time.time)

    @property
    def staff_id(self) -> int:
        return self.access.staff_id


class ConnectionManager:
    def __init__(self, *, max_per_staff: int = 10, send_timeout: float = 5.0) -> None:
        self._clients: list[Client] = []
        self.max_per_staff = max_per_staff
        self.send_timeout = send_timeout

    # ── connections ───────────────────────────────────────────────────────────

    async def connect(
        self, ws: WebSocket, access: AccessContext, expires_at: float, subprotocol: str | None = None,
        issued_at: float = 0,
    ) -> Client | None:
        """Accept the socket, or refuse it (returns None, socket closed) if this agent has too many open."""
        if sum(1 for c in self._clients if c.staff_id == access.staff_id) >= self.max_per_staff:
            await ws.close(code=CLOSE_TRY_LATER)
            return None
        await ws.accept(subprotocol=subprotocol)
        client = Client(ws=ws, access=access, expires_at=expires_at, issued_at=issued_at)
        self._clients.append(client)
        logger.debug("WebSocket connected (staff %s); total=%d", access.staff_id, len(self._clients))
        return client

    def disconnect(self, client: Client) -> None:
        if client in self._clients:
            self._clients.remove(client)
        logger.debug("WebSocket disconnected (staff %s); total=%d", client.staff_id, len(self._clients))

    def clients(self) -> tuple[Client, ...]:
        return tuple(self._clients)

    @property
    def client_count(self) -> int:
        return len(self._clients)

    # ── delivery ──────────────────────────────────────────────────────────────

    async def send(self, clients: Iterable[Client], message: dict) -> int:
        """Send ``message`` to exactly these clients; a client that fails or stalls is dropped. Returns how many got it."""
        delivered = 0
        for client in tuple(clients):
            if client.expires_at <= time.time():
                await self.close(client, CLOSE_POLICY, "token expired")
                continue
            try:
                await asyncio.wait_for(client.ws.send_json(message), timeout=self.send_timeout)
                delivered += 1
            except Exception:
                self.disconnect(client)
        return delivered

    async def close(self, client: Client, code: int, reason: str = "") -> None:
        self.disconnect(client)
        try:
            await client.ws.close(code=code, reason=reason)
        except Exception:
            pass

    # ── keeping authority current ─────────────────────────────────────────────

    async def revalidate(
        self, load_access: Callable[[int], Awaitable[AccessContext | None]], now: float | None = None
    ) -> dict:
        """Close clients whose token expired or whose account is gone/disabled; refresh everyone else's access.

        ``load_access(staff_id)`` returns the staff member's current ``AccessContext`` (or None). One load per
        staff member however many sockets they hold.
        """
        now = time.time() if now is None else now
        summary = {"expired": 0, "deactivated": 0, "refreshed": 0}
        fresh: dict[int, AccessContext | None] = {}
        for client in self.clients():
            if client.expires_at <= now:
                await self.close(client, CLOSE_POLICY, "token expired")
                summary["expired"] += 1
                continue
            if client.staff_id not in fresh:
                fresh[client.staff_id] = await load_access(client.staff_id)
            access = fresh[client.staff_id]
            if access is None or not access.is_active:
                await self.close(client, CLOSE_POLICY, "account no longer active")
                summary["deactivated"] += 1
            elif not access.token_is_current(client.issued_at):
                await self.close(client, CLOSE_POLICY, "password changed")
                summary["deactivated"] += 1
            else:
                client.access = access
                summary["refreshed"] += 1
        return summary


def _build() -> ConnectionManager:
    from app.core.config import settings

    return ConnectionManager(
        max_per_staff=settings.WS_MAX_CONNECTIONS_PER_STAFF, send_timeout=settings.WS_SEND_TIMEOUT_SECONDS
    )


manager = _build()
