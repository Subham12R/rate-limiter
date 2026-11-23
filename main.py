"""FastAPI demo: rate limiting as middleware.

In-memory by default (single process). Set REDIS_URL to share limits across
processes and servers through Redis.

Run:  uv run uvicorn main:app --reload
      REDIS_URL=redis://localhost:6379/0 uv run uvicorn main:app
Try:  for i in $(seq 25); do curl -s -o /dev/null -w "%{http_code}\n" localhost:8000/search; done
"""
import os
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from limiter import RateLimiter
from redis_limiter import RedisRateLimiter, make_redis

app = FastAPI()
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

UI_EXEMPT_PATHS = {"/", "/api/config", "/api/backend", "/favicon.ico"}

DEFAULT_REDIS_URL = "redis://localhost:6379/0"
REDIS_URL = os.getenv("REDIS_URL")
active_backend: Literal["in-memory", "redis"] = "redis" if REDIS_URL else "in-memory"
active_redis_url: str = REDIS_URL or DEFAULT_REDIS_URL
redis_client = make_redis(active_redis_url) if active_backend == "redis" else None


def build(limit: int, burst: int | None = None, *, name: str, fail_open: bool = True):
    if redis_client is None:
        return RateLimiter.per_minute(limit, burst)
    return RedisRateLimiter.per_minute(
        limit, burst, client=redis_client, prefix=f"rl:{name}", fail_open=fail_open
    )


def _init_limiters():
    limiters = {
        "/search": build(100, 20, name="search"),
        "/login": build(10, 5, name="login", fail_open=False),  # fail closed if Redis is down
    }
    default_limiter = build(300, 50, name="default")
    return limiters, default_limiter


# One limiter per rule. Keys include the route, so a client's /login budget
# never eats into their /search budget.
LIMITERS, DEFAULT_LIMITER = _init_limiters()


def _redis_ping() -> bool:
    if redis_client is None:
        return False
    try:
        return bool(redis_client.ping())
    except Exception:
        return False


class BackendSwitchRequest(BaseModel):
    backend: Literal["in-memory", "redis"]
    redis_url: str | None = None


def client_id(request: Request) -> str:
    # Prefer an authenticated identity. Only trust X-Forwarded-For if it is set
    # by YOUR proxy; otherwise clients can spoof it to dodge limits.
    return request.headers.get("x-api-key") or request.client.host


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    route = request.url.path
    if route in UI_EXEMPT_PATHS or route.startswith("/static/"):
        return await call_next(request)
    limiter = LIMITERS.get(route, DEFAULT_LIMITER)
    # The Redis call is blocking (~1 ms), so keep it off the event loop.
    result = await run_in_threadpool(limiter.check, f"{client_id(request)}:{route}")
    headers = result.headers()
    headers["X-RateLimit-Backend"] = active_backend
    if getattr(result, "degraded", False):
        headers["X-RateLimit-Degraded"] = "true"
    if not result.allowed:
        return JSONResponse(
            {"error": "rate_limited", "retry_after_seconds": result.retry_after_s},
            status_code=429,
            headers=headers,
        )
    response = await call_next(request)
    response.headers.update(headers)
    return response


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/config")
async def config():
    connected = await run_in_threadpool(_redis_ping) if active_backend == "redis" else None
    return {
        "backend": active_backend,
        "redis_url": active_redis_url,
        "redis_connected": connected,
        "routes": [
            {
                "path": "/search",
                "method": "GET",
                "limit_per_minute": 100,
                "burst": 20,
                "fail_open": True,
            },
            {
                "path": "/login",
                "method": "POST",
                "limit_per_minute": 10,
                "burst": 5,
                "fail_open": False,
            },
        ],
    }


@app.post("/api/backend")
async def switch_backend(payload: BackendSwitchRequest):
    global active_backend, active_redis_url, redis_client, LIMITERS, DEFAULT_LIMITER
    active_backend = payload.backend
    if payload.redis_url:
        active_redis_url = payload.redis_url.strip()
    if active_backend == "redis":
        redis_client = make_redis(active_redis_url)
    else:
        redis_client = None
    LIMITERS, DEFAULT_LIMITER = _init_limiters()
    return await config()


@app.get("/search")
def search():
    return {"results": []}


@app.post("/login")
def login():
    return {"ok": True}
