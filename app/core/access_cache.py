"""A short per-process cache of the slow-moving part of an agent's access.

``load_access_context`` reads the agent's staff row on **every** request — that row carries the enabled and admin
flags, the global permissions and the primary role, so disabling, demoting or re-permissioning an agent applies on
their very next request. What is cached is the rest: extra-department roles, role permissions, team membership and
the visible departments (four queries). An entry is reused only while

  * it is younger than ``ACCESS_CACHE_TTL_SECONDS``,
  * the staff row's primary department and role still match the ones it was built for, and
  * no invalidating event has arrived: the outbox worker clears it on ``dept.*`` / ``team.*`` and drops one agent's
    entry on ``staff.*`` (every process sees every event), and a command read through the API that has finished
    clears it at once in that process.

What can lag, by at most the TTL: a change made outside the bridge and its events (a role's permission map edited
in osTicket's UI, a team membership changed there).
"""

import time

from app.core.config import settings

MAX_ENTRIES = 2048
_INVALIDATING_AGGREGATES = {"dept", "team"}   # touch many agents' departments/teams: drop everything
_COMMAND_PREFIXES = ("staff.", "dept.", "team.")


class AccessGrantsCache:
    def __init__(self) -> None:
        # staff_id -> (stored_at, (primary dept id, primary role id), grants)
        self._entries: dict[int, tuple[float, tuple[int, int], dict]] = {}

    def get(self, staff_id: int, fingerprint: tuple[int, int], now: float | None = None) -> dict | None:
        ttl = settings.ACCESS_CACHE_TTL_SECONDS
        entry = self._entries.get(staff_id)
        if not ttl or entry is None:
            return None
        stored_at, stored_fingerprint, grants = entry
        if stored_fingerprint != fingerprint or (now if now is not None else time.monotonic()) - stored_at >= ttl:
            self._entries.pop(staff_id, None)
            return None
        return grants

    def put(self, staff_id: int, fingerprint: tuple[int, int], grants: dict, now: float | None = None) -> None:
        if not settings.ACCESS_CACHE_TTL_SECONDS:
            return
        if len(self._entries) >= MAX_ENTRIES and staff_id not in self._entries:
            self._entries.pop(next(iter(self._entries)))       # oldest inserted
        self._entries[staff_id] = (now if now is not None else time.monotonic(), fingerprint, grants)

    def invalidate(self, staff_id: int | None = None) -> None:
        if staff_id is None:
            self._entries.clear()
        else:
            self._entries.pop(staff_id, None)

    def on_event(self, aggregate_type: str, aggregate_id: str | int | None) -> None:
        """An outbox event happened: drop whatever it may have changed."""
        kind = (aggregate_type or "").split(".")[0]
        if kind in _INVALIDATING_AGGREGATES:
            self.invalidate()
        elif kind == "staff":
            try:
                self.invalidate(int(aggregate_id))
            except (TypeError, ValueError):
                self.invalidate()

    def on_command_finished(self, command_type: str) -> None:
        if (command_type or "").startswith(_COMMAND_PREFIXES):
            self.invalidate()

    def __len__(self) -> int:
        return len(self._entries)


access_cache = AccessGrantsCache()
