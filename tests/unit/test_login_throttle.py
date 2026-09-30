"""LoginThrottle — the lockout osTicket's session-based strike counter cannot provide to an API."""

from app.core.login_throttle import LoginThrottle


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _throttle(max_attempts=3, lockout=60):
    clock = Clock()
    return LoginThrottle(max_attempts, lockout, clock=clock), clock


def _fail(throttle, times, user="alice", ip="1.1.1.1"):
    for _ in range(times):
        throttle.record_failure(user, ip)


def test_a_fresh_pair_is_not_locked():
    throttle, _ = _throttle()
    assert throttle.retry_after("alice", "1.1.1.1") == 0


def test_the_pair_locks_after_max_attempts():
    throttle, _ = _throttle(max_attempts=3, lockout=60)
    _fail(throttle, 2)
    assert throttle.retry_after("alice", "1.1.1.1") == 0
    _fail(throttle, 1)
    assert throttle.retry_after("alice", "1.1.1.1") == 60


def test_the_lockout_counts_down_and_then_lifts():
    throttle, clock = _throttle(3, 60)
    _fail(throttle, 3)
    clock.now += 45
    assert throttle.retry_after("alice", "1.1.1.1") == 15
    clock.now += 16
    assert throttle.retry_after("alice", "1.1.1.1") == 0


def test_another_address_can_still_log_in_as_the_same_user():
    """Hammering an account from one address must not lock the real user out from theirs."""
    throttle, _ = _throttle()
    _fail(throttle, 3, ip="6.6.6.6")
    assert throttle.retry_after("alice", "6.6.6.6") > 0
    assert throttle.retry_after("alice", "1.1.1.1") == 0


def test_another_user_from_the_same_address_is_unaffected():
    throttle, _ = _throttle()
    _fail(throttle, 3, user="alice")
    assert throttle.retry_after("bob", "1.1.1.1") == 0


def test_a_distributed_guess_against_one_account_locks_it_everywhere():
    throttle, _ = _throttle(max_attempts=3, lockout=60)      # username-wide limit: 15
    for i in range(15):
        throttle.record_failure("alice", f"10.0.0.{i}")      # a different address every time
    assert throttle.retry_after("alice", "203.0.113.5") == 60


def test_usernames_are_case_and_whitespace_insensitive():
    throttle, _ = _throttle()
    _fail(throttle, 2, user="Alice")
    throttle.record_failure("  ALICE ", "1.1.1.1")
    assert throttle.retry_after("alice", "1.1.1.1") > 0


def test_a_success_clears_the_pair_but_not_the_username_wide_count():
    throttle, _ = _throttle(3, 60)
    _fail(throttle, 2)
    throttle.record_success("alice", "1.1.1.1")
    throttle.record_failure("alice", "1.1.1.1")               # 1 failure again, not 3
    assert throttle.retry_after("alice", "1.1.1.1") == 0


def test_old_failures_age_out_of_the_window():
    throttle, clock = _throttle(3, 60)
    _fail(throttle, 2)
    clock.now += 61
    _fail(throttle, 1)                                        # the first two no longer count
    assert throttle.retry_after("alice", "1.1.1.1") == 0


def test_reset_forgets_everything():
    throttle, _ = _throttle()
    _fail(throttle, 3)
    throttle.reset()
    assert throttle.retry_after("alice", "1.1.1.1") == 0


def test_memory_is_pruned_of_stale_entries(monkeypatch):
    import app.core.login_throttle as module

    monkeypatch.setattr(module, "_PRUNE_ABOVE", 50)
    throttle, clock = _throttle(3, 60)
    for i in range(40):
        throttle.record_failure(f"user{i}", "1.1.1.1")
    clock.now += 500                                          # all of it is stale now
    for i in range(20):
        throttle.record_failure(f"fresh{i}", "1.1.1.1")       # crosses the threshold -> prune
    assert len(throttle._by_pair) <= 25
