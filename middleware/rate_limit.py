import time
import math
from functools import wraps
from typing import Callable, Dict, Optional
from flask import request, Response
from utils.helpers import HttpError
from config import Config

class SlidingWindowStore:
    """Thread-safe sliding window rate limit counter with periodic pruning."""
    def __init__(self):
        self.store: Dict[str, Dict[str, Any]] = {}
        self.last_cleanup = time.time()

    def check_and_increment(self, key: str, window_seconds: int, max_requests: int):
        now = time.time()

        # Prune expired keys every 60s
        if now - self.last_cleanup > 60:
            self.prune(now)

        entry = self.store.get(key)
        if not entry or now > entry["reset_at"]:
            entry = {"count": 1, "reset_at": now + window_seconds}
            self.store[key] = entry
        else:
            entry["count"] += 1

        remaining = max(0, max_requests - entry["count"])
        reset_seconds = math.ceil(max(0, entry["reset_at"] - now))
        is_exceeded = entry["count"] > max_requests

        return is_exceeded, remaining, reset_seconds

    def prune(self, now: float):
        keys_to_delete = [k for k, v in self.store.items() if now > v["reset_at"]]
        for k in keys_to_delete:
            del self.store[k]
        self.last_cleanup = now

def get_client_ip(req) -> str:
    """Extracts client IP considering reverse proxies (PythonAnywhere / Render / Cloudflare)."""
    forwarded = req.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return req.remote_addr or "127.0.0.1"

def create_rate_limiter(
    window_seconds: int = 60,
    max_requests: int = 100,
    message: str = "Too many requests. Please slow down and try again later.",
    code: str = "TOO_MANY_REQUESTS",
    key_generator: Optional[Callable] = None
):
    store = SlidingWindowStore()
    keygen = key_generator or get_client_ip

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            key = keygen(request)
            is_exceeded, remaining, reset_seconds = store.check_and_increment(
                key=key,
                window_seconds=window_seconds,
                max_requests=max_requests
            )

            if is_exceeded:
                raise HttpError(
                    status=429,
                    message=message,
                    code=code,
                    retry_after_seconds=reset_seconds
                )

            res = fn(*args, **kwargs)

            # Attach RFC 6585 RateLimit headers
            if isinstance(res, Response):
                res.headers["RateLimit-Limit"] = str(max_requests)
                res.headers["RateLimit-Remaining"] = str(remaining)
                res.headers["RateLimit-Reset"] = str(reset_seconds)

            return res

        return wrapper

    return decorator

# ---- Pre-configured Limiters matching Node.js Express server ----

# Global API rate limiter: 600 req/min
global_limiter = create_rate_limiter(
    window_seconds=60,
    max_requests=Config.RATE_LIMIT_GLOBAL,
    message="Too many requests from this address. Please wait a minute.",
    code="RATE_LIMIT_GLOBAL"
)

# Auth rate limiter: 20 req/min in production
auth_limiter = create_rate_limiter(
    window_seconds=60,
    max_requests=Config.RATE_LIMIT_AUTH,
    message="Too many authentication attempts. Please wait a minute before trying again.",
    code="RATE_LIMIT_AUTH"
)

# Setup rate limiter: 25 req/min
setup_limiter = create_rate_limiter(
    window_seconds=60,
    max_requests=Config.RATE_LIMIT_SETUP,
    message="Too many setup attempts. Please slow down.",
    code="RATE_LIMIT_SETUP"
)

# Admin console rate limiter: 40 req/min
admin_limiter = create_rate_limiter(
    window_seconds=60,
    max_requests=Config.RATE_LIMIT_ADMIN,
    message="Too many admin requests. Please wait a moment.",
    code="RATE_LIMIT_ADMIN"
)

# Public tracking/queue rate limiter: 120 req/min
public_limiter = create_rate_limiter(
    window_seconds=60,
    max_requests=Config.RATE_LIMIT_PUBLIC,
    message="Too many public requests. Please slow down.",
    code="RATE_LIMIT_PUBLIC"
)
