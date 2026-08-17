"""Unit tests for the login failure limiter."""
from app.ratelimit import FailureLimiter


def test_locks_after_max_then_resets():
    lim = FailureLimiter(max_failures=3, window_seconds=60)
    assert lim.is_locked("k") == (False, 0)
    for _ in range(3):
        lim.register_failure("k")
    locked, retry = lim.is_locked("k")
    assert locked and retry > 0
    lim.reset("k")
    assert lim.is_locked("k") == (False, 0)


def test_failures_outside_window_do_not_lock():
    lim = FailureLimiter(max_failures=2, window_seconds=0)  # everything ages out instantly
    lim.register_failure("k")
    lim.register_failure("k")
    assert lim.is_locked("k") == (False, 0)


def test_keys_are_independent():
    lim = FailureLimiter(max_failures=1, window_seconds=60)
    lim.register_failure("a")
    assert lim.is_locked("a")[0] is True
    assert lim.is_locked("b")[0] is False
