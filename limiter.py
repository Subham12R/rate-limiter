
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Result:
    allowed: bool
    limit: int
    remaining: int
    reset_s: int         # seconds until the bucket is full again
    retry_after_s: int   # seconds until the request would fit (0 if allowed)

    def headers(self) -> dict[str, str]:
        h = {
            "RateLimit-Limit": str(self.limit),
            "RateLimit-Remaining": str(self.remaining),
            "RateLimit-Reset": str(self.reset_s),
        }
        if not self.allowed:
            h["Retry-After"] = str(max(1, self.retry_after_s))
        return h


class RateLimiter:
    def __init__(
        self,
        capacity: int,
        refill_per_sec: float,
        *,
        max_keys: int = 100_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if capacity < 1 or refill_per_sec <= 0:
            raise ValueError("capacity must be >= 1 and refill_per_sec > 0")
        self.capacity = capacity
        self.rate = refill_per_sec
        self._max_keys = max_keys
        self._clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, last_ts)
        self._lock = threading.Lock()

    @classmethod
    def per_minute(cls, limit: int, burst: int | None = None, **kw) -> "RateLimiter":
        return cls(burst or limit, limit / 60, **kw)

    def check(self, key: str, cost: int = 1) -> Result:
        if cost < 1 or cost > self.capacity:
            raise ValueError("cost must be between 1 and capacity")
        now = self._clock()
        with self._lock:
            tokens, last = self._buckets.get(key, (float(self.capacity), now))
            # Lazy refill: no timers, just math on each request.
            tokens = min(self.capacity, tokens + (now - last) * self.rate)

            allowed = tokens >= cost
            retry = 0.0
            if allowed:
                tokens -= cost
            else:
                retry = (cost - tokens) / self.rate

            self._buckets[key] = (tokens, now)
            if len(self._buckets) > self._max_keys:
                self._evict_idle(now)

        return Result(
            allowed=allowed,
            limit=self.capacity,
            remaining=int(tokens),
            reset_s=math.ceil((self.capacity - tokens) / self.rate),
            retry_after_s=math.ceil(retry),
        )

    def _evict_idle(self, now: float) -> None:
        # A bucket that would already be full again carries no information.
        for k, (tokens, last) in list(self._buckets.items()):
            if tokens + (now - last) * self.rate >= self.capacity:
                del self._buckets[k]


if __name__ == "__main__":
    limiter = RateLimiter.per_minute(limit=60, burst=5)  # 1 req/s, burst of 5
    for i in range(7):
        r = limiter.check("user-42")
        print(i + 1, "allowed" if r.allowed else "429", r.headers())
