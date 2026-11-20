# rate-limiter

v1: single-process, in-memory token-bucket rate limiter.

- `limiter.py`: the limiter (`RateLimiter`, `Result`). No dependencies.
- `main.py`: FastAPI demo that applies it as middleware.
- `test.py`: tests.

## Usage

```python
from limiter import RateLimiter

limiter = RateLimiter.per_minute(limit=60, burst=5)  # 1 req/s sustained, burst of 5
r = limiter.check("user-42")
if not r.allowed:
    ...  # respond 429 with r.headers()
```

## Run

```
uv run uvicorn main:app --reload
uv run pytest
```

## Limitation

State lives in process memory. With several app servers, each enforces its
own limit, so a client hitting N servers gets N times the limit. A shared
store (Redis) is the next step.
