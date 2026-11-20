"""FastAPI demo: rate limiting as middleware.

In-memory by default (single process). Set REDIS_URL to share limits across
processes and servers through Redis.

Run:  uv run uvicorn main:app --reload
      REDIS_URL=redis://localhost:6379/0 uv run uvicorn main:app
Try:  for i in $(seq 25); do curl -s -o /dev/null -w "%{http_code}\n" localhost:8000/search; done
"""
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from limiter import RateLimiter
from redis_limiter import RedisRateLimiter, make_redis

app = FastAPI()

REDIS_URL = os.getenv("REDIS_URL")
redis_client = make_redis(REDIS_URL) if REDIS_URL else None


def build(limit: int, burst: int | None = None, *, name: str, fail_open: bool = True):
    if redis_client is None:
        return RateLimiter.per_minute(limit, burst)
    return RedisRateLimiter.per_minute(
        limit, burst, client=redis_client, prefix=f"rl:{name}", fail_open=fail_open
    )


# One limiter per rule. Keys include the route, so a client's /login budget
# never eats into their /search budget.
LIMITERS = {
    "/search": build(100, 20, name="search"),
    "/login": build(10, 5, name="login", fail_open=False),  # fail closed if Redis is down
}
DEFAULT_LIMITER = build(300, 50, name="default")


def client_id(request: Request) -> str:
    # Prefer an authenticated identity. Only trust X-Forwarded-For if it is set
    # by YOUR proxy; otherwise clients can spoof it to dodge limits.
    return request.headers.get("x-api-key") or request.client.host


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    route = request.url.path
    limiter = LIMITERS.get(route, DEFAULT_LIMITER)
    # The Redis call is blocking (~1 ms), so keep it off the event loop.
    result = await run_in_threadpool(limiter.check, f"{client_id(request)}:{route}")
    if not result.allowed:
        return JSONResponse(
            {"error": "rate_limited", "retry_after_seconds": result.retry_after_s},
            status_code=429,
            headers=result.headers(),
        )
    response = await call_next(request)
    response.headers.update(result.headers())
    return response


@app.get("/search")
def search():
    return {"results": []}


@app.post("/login")
def login():
    return {"ok": True}
