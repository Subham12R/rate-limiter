"""Redis-backed token bucket. Same check(key, cost) interface as limiter.RateLimiter.

State is one Redis HASH per key ({tokens, ts}). Refill, check and decrement
run in a single Lua script, so concurrent app servers can't race. Time comes
from Redis TIME, so app-server clock skew doesn't matter.

If Redis is slow or down:
  fail_open=True   -> fall back to an in-process bucket (abuse stays bounded)
  fail_open=False  -> reject the request
A small circuit breaker stops calling Redis after repeated failures.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass

import redis
from redis.backoff import NoBackoff
from redis.retry import Retry

from limiter import RateLimiter, Result

LUA_TOKEN_BUCKET = """
local key  = KEYS[1]
local cap  = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])   -- tokens per second
local cost = tonumber(ARGV[3])

local t   = redis.call('TIME')
local now = t[1] * 1000 + math.floor(t[2] / 1000)   -- ms

local d      = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(d[1])
local ts     = tonumber(d[2])
if tokens == nil then
  tokens = cap
  ts = now
end

tokens = math.min(cap, tokens + math.max(0, now - ts) / 1000 * rate)

local allowed  = 0
local retry_ms = 0
if tokens >= cost then
  tokens  = tokens - cost
  allowed = 1
else
  retry_ms = math.ceil((cost - tokens) / rate * 1000)
end

redis.call('HSET', key, 'tokens', tokens, 'ts', now)
redis.call('PEXPIRE', key, math.ceil(cap / rate * 1000) * 2)

local reset_ms = math.ceil((cap - tokens) / rate * 1000)
return {allowed, math.floor(tokens), retry_ms, reset_ms}
"""


@dataclass(frozen=True)
class RedisResult(Result):
    degraded: bool = False  # True if Redis was unavailable for this decision


class RedisRateLimiter:
    def __init__(
        self,
        capacity: int,
        refill_per_sec: float,
        *,
        client: redis.Redis,
        prefix: str = "rl",
        fail_open: bool = True,
        fallback_share: float = 1.0,
        breaker_threshold: int = 5,
        breaker_cooldown_s: float = 5.0,
    ) -> None:
        if capacity < 1 or refill_per_sec <= 0:
            raise ValueError("capacity must be >= 1 and refill_per_sec > 0")
        self.capacity = capacity
        self.rate = refill_per_sec
        self._script = client.register_script(LUA_TOKEN_BUCKET)
        self._prefix = prefix
        self._fail_open = fail_open
        # Local fallback enforces `fallback_share` of the rule (e.g. 1/N for N
        # app instances) so the aggregate stays near the global limit.
        self._fallback = RateLimiter(
            max(1, int(capacity * fallback_share)), refill_per_sec * fallback_share
        )
        self._threshold = breaker_threshold
        self._cooldown = breaker_cooldown_s
        self._failures = 0
        self._open_until = 0.0  # circuit breaker: skip Redis until this time

    @classmethod
    def per_minute(cls, limit: int, burst: int | None = None, **kw) -> "RedisRateLimiter":
        return cls(burst or limit, limit / 60, **kw)

    def check(self, key: str, cost: int = 1) -> RedisResult:
        if cost < 1 or cost > self.capacity:
            raise ValueError("cost must be between 1 and capacity")

        if time.monotonic() >= self._open_until:
            try:
                allowed, remaining, retry_ms, reset_ms = self._script(
                    keys=[f"{self._prefix}:{key}"], args=[self.capacity, self.rate, cost]
                )
            except redis.RedisError:
                self._record_failure()
            else:
                self._failures = 0
                return RedisResult(
                    allowed=bool(allowed),
                    limit=self.capacity,
                    remaining=int(remaining),
                    reset_s=math.ceil(int(reset_ms) / 1000),
                    retry_after_s=math.ceil(int(retry_ms) / 1000),
                )

        # Redis unavailable (error, timeout, or breaker open).
        if self._fail_open:
            r = self._fallback.check(key, cost)
            return RedisResult(
                r.allowed, r.limit, r.remaining, r.reset_s, r.retry_after_s, degraded=True
            )
        return RedisResult(False, self.capacity, 0, 1, 1, degraded=True)

    def _record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self._threshold:
            self._open_until = time.monotonic() + self._cooldown
            self._failures = 0


def make_redis(url: str = "redis://localhost:6379/0") -> redis.Redis:
    """Redis client with tight timeouts (10 ms) and retries off.

    redis-py's default backoff retries can add seconds when Redis is down, so
    failures are handled by the limiter's fail-open/closed path instead.
    """
    return redis.Redis.from_url(
        url,
        socket_timeout=0.010,
        socket_connect_timeout=0.050,
        retry=Retry(NoBackoff(), 0),
        decode_responses=True,
    )
