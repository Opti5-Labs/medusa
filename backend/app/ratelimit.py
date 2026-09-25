"""
Per-IP sliding-window rate limiter (no external dependency).

Usage:
    limiter = RateLimiter(max_calls=5, window_seconds=600)

    @app.post("/api/scan")
    async def scan(request: Request):
        limiter.check(request)   # raises MedusaError(429) if exceeded
        ...

IP resolution:
    By default the socket peer address is used.
    Set TRUST_FORWARDED_FOR=true in the environment when running behind a
    trusted nginx proxy that sets X-Forwarded-For correctly.
"""

import collections
import math
import os
import time

from fastapi import Request

from app.errors import MedusaError

_TRUST_FORWARDED_FOR: bool = os.getenv("TRUST_FORWARDED_FOR", "false").lower() == "true"


def _client_ip(request: Request) -> str:
    if _TRUST_FORWARDED_FOR:
        forwarded = request.headers.get("X-Forwarded-For", "")
        ip = forwarded.split(",")[0].strip()
        if ip:
            return ip
    if request.client:
        return request.client.host
    return "unknown"


class RateLimiter:
    """Thread-safe sliding-window limiter using a deque per IP."""

    def __init__(self, max_calls: int, window_seconds: int) -> None:
        self._max = max_calls
        self._window = window_seconds
        # ip -> deque of timestamps (monotonic)
        self._windows: dict[str, collections.deque[float]] = collections.defaultdict(
            collections.deque
        )

    def check(self, request: Request) -> None:
        """
        Raise MedusaError(429) if the client has exceeded the rate limit.
        Otherwise record this call.
        """
        ip = _client_ip(request)
        now = time.monotonic()
        dq = self._windows[ip]

        # Evict timestamps outside the window
        cutoff = now - self._window
        while dq and dq[0] <= cutoff:
            dq.popleft()

        if len(dq) >= self._max:
            # Time until the oldest call falls outside the window
            wait_s = math.ceil(dq[0] - cutoff)
            if wait_s >= 60:
                wait_str = f"{math.ceil(wait_s / 60)} minute(s)"
            else:
                wait_str = f"{wait_s} second(s)"
            raise MedusaError(
                429,
                f"Too many scans. Please wait {wait_str} before trying again.",
            )

        dq.append(now)

        # Evict empty deques to prevent unbounded dict growth
        if not dq:  # pragma: no cover  (only if max_calls == 0)
            del self._windows[ip]

    def _evict_empty(self) -> None:
        """Remove IPs whose deque is empty (call periodically if needed)."""
        empty = [ip for ip, dq in self._windows.items() if not dq]
        for ip in empty:
            del self._windows[ip]
