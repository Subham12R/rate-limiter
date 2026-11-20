import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import redis

if shutil.which("redis-server") is None:
    pytest.skip("redis-server not installed", allow_module_level=True)

from redis_limiter import RedisRateLimiter, make_redis  # noqa: E402

PORT = 6390
URL = f"redis://localhost:{PORT}/0"


@pytest.fixture(scope="session", autouse=True)
def redis_server():
    proc = subprocess.Popen(
        ["redis-server", "--port", str(PORT), "--save", "", "--appendonly", "no"],
        stdout=subprocess.DEVNULL,
    )
    admin = redis.Redis(port=PORT)
    for _ in range(50):
        try:
            admin.ping()
            break
        except redis.ConnectionError:
            time.sleep(0.1)
    yield
    proc.terminate()
    proc.wait()


@pytest.fixture
def client():
    redis.Redis(port=PORT).flushall()
    return make_redis(URL)


def dead(capacity=3, rate=0.001, **kw):
    return RedisRateLimiter(capacity, rate, client=make_redis("redis://localhost:1/0"), **kw)


def test_burst_then_deny_then_refill(client):
    lim = RedisRateLimiter(3, 1, client=client)
    results = [lim.check("u") for _ in range(4)]
    assert [r.allowed for r in results] == [True, True, True, False]
    assert [r.remaining for r in results[:3]] == [2, 1, 0]
    assert results[3].retry_after_s == 1
    assert not results[3].degraded
    time.sleep(1.1)
    assert lim.check("u").allowed


def test_keys_are_independent(client):
    lim = RedisRateLimiter(1, 0.01, client=client)
    assert lim.check("a").allowed
    assert not lim.check("a").allowed
    assert lim.check("b").allowed


def test_limiters_with_different_prefixes_do_not_share_state(client):
    a = RedisRateLimiter(1, 0.01, client=client, prefix="rl:a")
    b = RedisRateLimiter(1, 0.01, client=client, prefix="rl:b")
    assert a.check("u").allowed
    assert b.check("u").allowed


def test_atomic_under_concurrency(client):
    # 400 concurrent requests, bucket of 50, negligible refill: exactly 50 pass.
    lim = RedisRateLimiter(50, 0.001, client=client)
    with ThreadPoolExecutor(max_workers=32) as pool:
        results = list(pool.map(lambda _: lim.check("u"), range(400)))
    assert sum(r.allowed for r in results) == 50


def test_state_is_shared_between_limiter_instances(client):
    # Two instances stand in for two app servers.
    s1 = RedisRateLimiter(5, 0.001, client=client)
    s2 = RedisRateLimiter(5, 0.001, client=client)
    allowed = sum(lim.check("u").allowed for _ in range(5) for lim in (s1, s2))
    assert allowed == 5


def test_idle_keys_get_ttl(client):
    RedisRateLimiter(10, 1, client=client).check("u")
    ttl = redis.Redis(port=PORT).pttl("rl:u")
    assert 0 < ttl <= 20_000  # 2 x time-to-refill = 2 x 10 s


def test_headers(client):
    lim = RedisRateLimiter(1, 0.5, client=client)
    ok, denied = lim.check("u"), lim.check("u")
    assert ok.headers()["RateLimit-Limit"] == "1"
    assert "Retry-After" not in ok.headers()
    assert denied.headers()["Retry-After"] == "2"


def test_cost_validation(client):
    with pytest.raises(ValueError):
        RedisRateLimiter(2, 1, client=client).check("u", cost=3)


def test_fail_open_uses_bounded_local_fallback():
    lim = dead(fail_open=True)
    results = [lim.check("u") for _ in range(5)]
    assert [r.allowed for r in results] == [True, True, True, False, False]
    assert all(r.degraded for r in results)


def test_fail_closed_rejects():
    r = dead(fail_open=False).check("u")
    assert not r.allowed and r.degraded


def test_fallback_share_divides_limit():
    lim = dead(capacity=10, fail_open=True, fallback_share=0.5)
    assert sum(lim.check("u").allowed for _ in range(10)) == 5


def test_circuit_breaker_stops_calling_redis():
    calls = []

    def boom(**kwargs):
        calls.append(1)
        raise redis.ConnectionError("down")

    lim = dead(breaker_threshold=2, breaker_cooldown_s=60)
    lim._script = boom
    for _ in range(6):
        lim.check("u")
    assert len(calls) == 2  # breaker opened after 2 failures
