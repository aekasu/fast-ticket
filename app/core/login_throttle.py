"""Login lockout, kept by the API.

osTicket's own agent-login strike counter lives in the PHP session, which an API client can discard
by simply not sending a cookie — so it would never trigger here. This tracks failed *credential*
attempts (not policy refusals such as 2FA or a forced password change) in memory:

  * per (username, client address): ``max_attempts`` failures lock that pair out for ``lockout_seconds``.
    Keying on the pair means someone hammering an account from their own address cannot lock the real
    user out from theirs;
  * per username alone, at 5x the threshold: a distributed guess against one account is still stopped.

Counters are per process. With several API workers each keeps its own, so the effective allowance is
multiplied by the worker count; the per-IP limit on the login route (slowapi, RATE_LIMIT_STORAGE_URI)
is what a shared store such as Redis would tighten.
"""

import math
import time
from dataclasses import dataclass
from typing import Callable

_PRUNE_ABOVE = 10_000


@dataclass
class _Entry:
    failures: int = 0
    window_start: float = 0.0
    locked_until: float = 0.0


class LoginThrottle:
    def __init__(
        self,
        max_attempts: int,
        lockout_seconds: int,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.max_attempts = max_attempts
        self.user_max_attempts = max_attempts * 5
        self.lockout_seconds = lockout_seconds
        self._clock = clock
        self._by_pair: dict[tuple[str, str], _Entry] = {}
        self._by_user: dict[str, _Entry] = {}

    @staticmethod
    def _normalise(username: str) -> str:
        return username.strip().lower()[:128]

    def retry_after(self, username: str, ip: str) -> int:
        """Seconds until this attempt may be made, 0 if it may be made now."""
        user = self._normalise(username)
        now = self._clock()
        remaining = max(
            self._locked_for(self._by_pair.get((user, ip)), now),
            self._locked_for(self._by_user.get(user), now),
        )
        return math.ceil(remaining) if remaining > 0 else 0

    def record_failure(self, username: str, ip: str) -> None:
        user = self._normalise(username)
        now = self._clock()
        self._bump(self._by_pair.setdefault((user, ip), _Entry()), now, self.max_attempts)
        self._bump(self._by_user.setdefault(user, _Entry()), now, self.user_max_attempts)
        if len(self._by_pair) + len(self._by_user) > _PRUNE_ABOVE:
            self._prune(now)

    def record_success(self, username: str, ip: str) -> None:
        """A correct login clears that pair; the username-wide count keeps running."""
        self._by_pair.pop((self._normalise(username), ip), None)

    def reset(self) -> None:
        self._by_pair.clear()
        self._by_user.clear()

    # ── internals ─────────────────────────────────────────────────────────────

    @staticmethod
    def _locked_for(entry: _Entry | None, now: float) -> float:
        return (entry.locked_until - now) if entry else 0.0

    def _bump(self, entry: _Entry, now: float, limit: int) -> None:
        if entry.failures and now - entry.window_start > self.lockout_seconds:
            entry.failures = 0   # the old failures are outside the window
        if not entry.failures:
            entry.window_start = now
        entry.failures += 1
        if entry.failures >= limit:
            entry.locked_until = now + self.lockout_seconds
            entry.failures = 0

    def _prune(self, now: float) -> None:
        def stale(entry: _Entry) -> bool:
            return entry.locked_until <= now and now - entry.window_start > self.lockout_seconds

        self._by_pair = {k: e for k, e in self._by_pair.items() if not stale(e)}
        self._by_user = {k: e for k, e in self._by_user.items() if not stale(e)}
