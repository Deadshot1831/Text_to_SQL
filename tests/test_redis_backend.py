"""Redis-backed rate-limit + revocation, exercised with in-process fakeredis."""
import datetime as dt

import fakeredis

from app.auth import _Revocations
from app.ratelimit import RedisFailureLimiter


def _client():
    return fakeredis.FakeRedis(decode_responses=True)


def test_redis_failure_limiter_locks_and_resets():
    lim = RedisFailureLimiter(_client(), max_failures=3, window_seconds=60)
    assert lim.is_locked("k") == (False, 0)
    for _ in range(3):
        lim.register_failure("k")
    locked, retry = lim.is_locked("k")
    assert locked and retry > 0
    lim.reset("k")
    assert lim.is_locked("k") == (False, 0)


def test_redis_failure_limiter_keys_independent():
    lim = RedisFailureLimiter(_client(), max_failures=1, window_seconds=60)
    lim.register_failure("a")
    assert lim.is_locked("a")[0] is True
    assert lim.is_locked("b")[0] is False


def test_redis_revocation_roundtrip():
    rev = _Revocations(_client())
    exp = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5)
    assert rev.is_revoked("jti1") is False
    rev.revoke("jti1", exp)
    assert rev.is_revoked("jti1") is True
    assert rev.is_revoked("jti2") is False
