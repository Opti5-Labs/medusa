"""
Per-IP sliding-window rate limiter (no external dependency).

Usage:
    limiter = RateLimiter(max_calls=5, window_seconds=600, what="scans")

    @app.post("/api/scan")
    async def scan(request: Request):
        limiter.check(request)   # raises MedusaError(429) if exceeded
        ...

IP resolution:
    By default the socket peer address is used.
    Set TRUST_FORWARDED_FOR=true when running behind the nginx proxy in deploy/.
    The *last* X-Forwarded-For entry is used: nginx appends the real peer
    address, while earlier entries are whatever the client sent.
"""

import collections
import math
import time

from fastapi import Request

from app import config
from app.errors import MedusaError

# Sweep idle IPs once the table grows past this many entries.
_SWEEP_THRESHOLD = 10_000


def _client_ip(request: Request) -> str:
    if config.TRUST_FORWARDED_FOR:
        forwarded = request.headers.get("X-Forwarded-For", "")
        ip = forwarded.split(",")[-1].strip()
        if ip:
            return ip
    if request.client:
        return request.client.host
    return "unknown"


class RateLimiter:
    """Sliding-window limiter using a deque of timestamps per IP."""

    def __init__(
        self, max_calls: int, window_seconds: int, what: str = "scans"
    ) -> None:
        self._max = max_calls
        self._window = window_seconds
        self._what = what
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
        cutoff = now - self._window
        if len(self._windows) > _SWEEP_THRESHOLD:
            self._evict_idle(cutoff)
        dq = self._windows[ip]

        # Evict timestamps outside the window
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
                f"Too many {self._what}. Please wait {wait_str} before trying again.",
            )

        dq.append(now)

    def _evict_idle(self, cutoff: float) -> None:
        """Remove IPs with no calls inside the current window."""
        idle = [ip for ip, dq in self._windows.items() if not dq or dq[-1] <= cutoff]
        for ip in idle:
            del self._windows[ip]
