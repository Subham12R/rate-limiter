# rate-limiter

Token-bucket rate limiter with two backends and the same interface:

- `limiter.py`: in-memory, single process. No dependencies.
- `redis_limiter.py`: Redis-backed, shared across processes and servers.
- `main.py`: FastAPI demo that applies either one as middleware.
- `test.py`, `test_redis.py`: tests (the Redis tests are skipped if `redis-server` isn't installed).

## Usage

```python
from limiter import RateLimiter

limiter = RateLimiter.per_minute(limit=60, burst=5)  # 1 req/s sustained, burst of 5
r = limiter.check("user-42")
if not r.allowed:
    ...  # respond 429 with r.headers()
```

Redis backend, same call:

```python
from redis_limiter import RedisRateLimiter, make_redis

limiter = RedisRateLimiter.per_minute(
    60, burst=5, client=make_redis("redis://localhost:6379/0"), prefix="rl:api"
)
r = limiter.check("user-42")  # r.degraded is True if Redis was unavailable
```

How the Redis version works:

- One HASH per key (`tokens`, `ts`). Refill, check and decrement run in a single Lua script, so concurrent servers can't race.
- Time comes from Redis `TIME`, so server clock skew doesn't matter.
- Idle keys expire on their own (TTL = 2x time to refill).
- If Redis is slow or down (10 ms timeout, no retries): `fail_open=True` falls back to an in-process bucket, `fail_open=False` rejects. A circuit breaker stops calling Redis after repeated failures.
- Use a different `prefix` per rule, since one limiter holds one rule.

## Run

```
uv run uvicorn main:app --reload                                  # in-memory
REDIS_URL=redis://localhost:6379/0 uv run uvicorn main:app        # Redis
uv run pytest
```

## Limitation

The in-memory limiter keeps state in process memory. With several app servers,
each enforces its own limit, so a client hitting N servers gets N times the
limit. Use the Redis backend for that.
