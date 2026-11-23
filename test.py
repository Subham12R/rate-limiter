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


def test_ui_routes():
    from fastapi.testclient import TestClient

    from main import app

    http = TestClient(app)
    index = http.get("/")
    assert index.status_code == 200
    assert "text/html" in index.headers["content-type"]
    assert 'name="color-scheme"' in index.text

    css = http.get("/static/styles.css")
    assert css.status_code == 200
    js = http.get("/static/app.js")
    assert js.status_code == 200

    cfg = http.get("/api/config")
    assert cfg.status_code == 200
    data = cfg.json()
    assert data["backend"] in ("in-memory", "redis")
    assert {r["path"] for r in data["routes"]} == {"/search", "/login"}


def test_switch_backend_to_redis_and_back():
    from fastapi.testclient import TestClient

    from main import app

    http = TestClient(app)
    try:
        switched = http.post(
            "/api/backend",
            json={"backend": "redis", "redis_url": "redis://localhost:1/0"},
        )
        assert switched.status_code == 200
        body = switched.json()
        assert body["backend"] == "redis"
        assert body["redis_connected"] is False

        # /search fails open to local fallback and reports degraded
        search = http.get("/search", headers={"x-api-key": "redis-test"})
        assert search.status_code == 200
        assert search.headers["x-ratelimit-backend"] == "redis"
        assert search.headers["x-ratelimit-degraded"] == "true"

        # /login fails closed when Redis is unreachable
        login = http.post("/login", headers={"x-api-key": "redis-test"})
        assert login.status_code == 429
        assert login.headers["x-ratelimit-backend"] == "redis"
        assert login.headers["x-ratelimit-degraded"] == "true"
    finally:
        restored = http.post("/api/backend", json={"backend": "in-memory"})
        assert restored.status_code == 200
        assert restored.json()["backend"] == "in-memory"
