"""Small in-memory rate limiter (per process). Good enough for a single Render instance."""
import time
from collections import defaultdict, deque

from fastapi import HTTPException

_hits = defaultdict(deque)


def check(key: str, limit: int, window_seconds: int) -> None:
    now = time.monotonic()
    q = _hits[key]
    while q and now - q[0] > window_seconds:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(status_code=429, detail="too_many_requests")
    q.append(now)
    if len(_hits) > 20000:          # keep memory bounded
        for k in list(_hits.keys())[:5000]:
            _hits.pop(k, None)
