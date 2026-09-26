"""
Tests for ratelimit.py — sliding window, IP resolution, reset message.
"""

import pytest
from fastapi import Request

from app.errors import MedusaError
from app.ratelimit import RateLimiter


def _mock_request(ip: str = "127.0.0.1") -> Request:
    """Build a minimal fake Request with a given peer IP."""
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/scan",
        "headers": [],
        "client": (ip, 12345),
        "query_string": b"",
    }
    return Request(scope)


def test_allows_up_to_max():
    limiter = RateLimiter(max_calls=3, window_seconds=60)
    req = _mock_request()
    for _ in range(3):
        limiter.check(req)  # should not raise


def test_blocks_on_overflow():
    limiter = RateLimiter(max_calls=3, window_seconds=60)
    req = _mock_request()
    for _ in range(3):
        limiter.check(req)
    with pytest.raises(MedusaError) as exc_info:
        limiter.check(req)
    assert exc_info.value.status == 429
    assert "too many" in exc_info.value.message.lower()


def test_different_ips_independent():
    limiter = RateLimiter(max_calls=1, window_seconds=60)
    limiter.check(_mock_request("1.2.3.4"))
    limiter.check(_mock_request("5.6.7.8"))  # different IP, should not block


def test_window_expiry(monkeypatch):
    """Calls outside the window should not count."""
    limiter = RateLimiter(max_calls=2, window_seconds=10)
    req = _mock_request()

    # Simulate 2 old calls (15 seconds ago)
    import collections
    import time as _time

    old_time = _time.monotonic() - 15
    limiter._windows[req.client.host] = collections.deque([old_time, old_time])

    # These 2 old calls are outside window, so 2 new calls should succeed
    limiter.check(req)
    limiter.check(req)


def _proxied_request(forwarded_for: str) -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/scan",
        "headers": [(b"x-forwarded-for", forwarded_for.encode())],
        "client": ("127.0.0.1", 12345),
        "query_string": b"",
    }
    return Request(scope)


def test_forwarded_for_uses_proxy_appended_address(monkeypatch):
    """A client-supplied X-Forwarded-For entry must not let it dodge the limit."""
    monkeypatch.setattr("app.config.TRUST_FORWARDED_FOR", True)
    limiter = RateLimiter(max_calls=1, window_seconds=60)
    limiter.check(_proxied_request("1.1.1.1, 203.0.113.9"))
    with pytest.raises(MedusaError):
        limiter.check(_proxied_request("2.2.2.2, 203.0.113.9"))


def test_message_names_what_is_limited():
    limiter = RateLimiter(
        max_calls=1, window_seconds=60, what="reproduce or debug runs"
    )
    limiter.check(_mock_request())
    with pytest.raises(MedusaError, match="reproduce or debug runs"):
        limiter.check(_mock_request())
