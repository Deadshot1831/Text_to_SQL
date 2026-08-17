"""A tiny in-memory failure limiter for brute-force protection on login.

ponytail: per-process dict + a global lock — fine for one API instance. Swap the
store for Redis if you run several instances behind a load balancer.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict


class FailureLimiter:
    def __init__(self, max_failures: int, window_seconds: int):
        self.max = max_failures
        self.window = window_seconds
        self._fails: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> None:
        kept = [t for t in self._fails.get(key, []) if now - t < self.window]
        if kept:
            self._fails[key] = kept
        else:
            self._fails.pop(key, None)

    def is_locked(self, key: str) -> tuple[bool, int]:
        """(locked, retry_after_seconds). Locked once >= max failures fall inside the window."""
        now = time.time()
        with self._lock:
            self._prune(key, now)
            times = self._fails.get(key, [])
            if len(times) >= self.max:
                retry = int(self.window - (now - min(times))) + 1
                return True, max(retry, 1)
            return False, 0

    def register_failure(self, key: str) -> None:
        now = time.time()
        with self._lock:
            self._fails[key].append(now)
            self._prune(key, now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)


if __name__ == "__main__":  # tiny self-check
    lim = FailureLimiter(max_failures=3, window_seconds=60)
    assert lim.is_locked("k") == (False, 0)
    for _ in range(3):
        lim.register_failure("k")
    locked, retry = lim.is_locked("k")
    assert locked and retry > 0, (locked, retry)
    lim.reset("k")
    assert lim.is_locked("k") == (False, 0)
    print("ratelimit self-check OK")
