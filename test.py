from concurrent.futures import ThreadPoolExecutor

import pytest

from limiter import RateLimiter


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


def make(capacity=3, rate=1.0, **kw):
    clock = FakeClock()
    return RateLimiter(capacity, rate, clock=clock, **kw), clock


def test_burst_then_deny():
    lim, _ = make(capacity=3, rate=1)
    results = [lim.check("u") for _ in range(4)]
    assert [r.allowed for r in results] == [True, True, True, False]
    assert [r.remaining for r in results[:3]] == [2, 1, 0]
    assert results[3].retry_after_s == 1


def test_refill_over_time():
    lim, clock = make(capacity=2, rate=1)
    lim.check("u"), lim.check("u")
    assert not lim.check("u").allowed
    clock.advance(1)
    assert lim.check("u").allowed
    assert not lim.check("u").allowed


def test_refill_caps_at_capacity():
    lim, clock = make(capacity=2, rate=1)
    lim.check("u")
    clock.advance(1000)
    assert lim.check("u").remaining == 1  # full bucket (2) minus this request


def test_keys_are_independent():
    lim, _ = make(capacity=1, rate=0.01)
    assert lim.check("a").allowed
    assert not lim.check("a").allowed
    assert lim.check("b").allowed


def test_headers():
    lim, _ = make(capacity=1, rate=0.5)
    ok, denied = lim.check("u"), lim.check("u")
    assert ok.headers() == {
        "RateLimit-Limit": "1",
        "RateLimit-Remaining": "0",
        "RateLimit-Reset": "2",
    }
    assert denied.headers()["Retry-After"] == "2"


def test_cost_validation():
    lim, _ = make(capacity=2)
    with pytest.raises(ValueError):
        lim.check("u", cost=3)


def test_thread_safety():
    lim, _ = make(capacity=50, rate=0.001)
    with ThreadPoolExecutor(max_workers=32) as pool:
        results = list(pool.map(lambda _: lim.check("u"), range(400)))
    assert sum(r.allowed for r in results) == 50


def test_idle_keys_are_evicted():
    lim, clock = make(capacity=1, rate=1, max_keys=10)
    for i in range(10):
        lim.check(f"u{i}")
    clock.advance(5)  # all buckets are full again
    lim.check("trigger")
    assert len(lim._buckets) <= 2


def test_fastapi_middleware():
    from fastapi.testclient import TestClient

    from main import app

    http = TestClient(app)
    codes = [http.get("/search").status_code for _ in range(21)]
    assert codes == [200] * 20 + [429]
    r = http.get("/search")
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) >= 1
    assert r.json()["error"] == "rate_limited"
    # a different route has its own budget
    ok = http.post("/login")
    assert ok.status_code == 200
    assert "ratelimit-remaining" in ok.headers
