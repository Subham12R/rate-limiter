"""FastAPI demo: in-memory rate limiting as middleware (v1, single process).

Run:  uv run uvicorn main:app --reload
Try:  for i in $(seq 25); do curl -s -o /dev/null -w "%{http_code}\n" localhost:8000/search; done
"""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from limiter import RateLimiter

app = FastAPI()

# One limiter per rule. Keys include the route, so a client's /login budget
# never eats into their /search budget.
LIMITERS = {
    "/search": RateLimiter.per_minute(100, burst=20),
    "/login": RateLimiter.per_minute(10, burst=5),
}
DEFAULT_LIMITER = RateLimiter.per_minute(300, burst=50)


def client_id(request: Request) -> str:
    # Prefer an authenticated identity. Only trust X-Forwarded-For if it is set
    # by YOUR proxy; otherwise clients can spoof it to dodge limits.
    return request.headers.get("x-api-key") or request.client.host


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    route = request.url.path
    limiter = LIMITERS.get(route, DEFAULT_LIMITER)
    # In-memory check takes microseconds, so it is fine to call inline.
    result = limiter.check(f"{client_id(request)}:{route}")
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
