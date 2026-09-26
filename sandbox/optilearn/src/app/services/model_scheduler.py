"""Priority-lane scheduler for AI-facing OptiLearn work.

The scheduler separates classroom-interactive requests from background work
without changing model tags, prompts, sampling, context, output budgets, or
thinking policy.
"""
from __future__ import annotations

import asyncio
import time
from collections import Counter
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any, TypeVar

from loguru import logger

from app.services import telemetry

LANE_LIVE_TRANSLATION = "live_translation"
LANE_STUDENT_CHAT = "student_chat"
LANE_ADMIN = "admin"
LANE_BACKGROUND = "background"
LANE_LIVE_CLASS_TRANSCRIPTION = "live_class_transcription"
LANE_LIVE_CLASS_TRANSLATION   = "live_class_translation"

_LANE_LIMITS = {
    LANE_LIVE_TRANSLATION: 1,
    LANE_STUDENT_CHAT: 1,
    LANE_ADMIN: 1,
    LANE_BACKGROUND: 1,
    LANE_LIVE_CLASS_TRANSCRIPTION: 1,
    LANE_LIVE_CLASS_TRANSLATION:   3,
}

_LANE_TIMEOUT_SECONDS = {
    LANE_LIVE_TRANSLATION: 180.0,
    LANE_STUDENT_CHAT: 240.0,
    LANE_ADMIN: 300.0,
    LANE_BACKGROUND: 600.0,
    LANE_LIVE_CLASS_TRANSCRIPTION: 60.0,
    LANE_LIVE_CLASS_TRANSLATION:   120.0,
}

_semaphores = {
    lane: asyncio.Semaphore(limit)
    for lane, limit in _LANE_LIMITS.items()
}
_active: Counter[str] = Counter()
_completed: Counter[str] = Counter()
_timed_out: Counter[str] = Counter()
_failed: Counter[str] = Counter()

T = TypeVar("T")


def normalize_lane(lane: str | None, route_type: str | None = None) -> str:
    if lane in _semaphores:
        return lane
    return lane_for_route(route_type or "")


def lane_for_route(route_type: str) -> str:
    route = (route_type or "").upper()
    if route == "TRANSLATION":
        return LANE_LIVE_TRANSLATION
    if route == "TUTOR":
        return LANE_STUDENT_CHAT
    if route == "ADMIN":
        return LANE_ADMIN
    return LANE_BACKGROUND


async def stream_with_lane(
    *,
    lane: str | None,
    feature: str,
    route_type: str,
    input_size: int,
    cache_status: str,
    generator_factory: Callable[[], AsyncGenerator[str, None]],
) -> AsyncGenerator[str, None]:
    lane_name = normalize_lane(lane, route_type)
    semaphore = _semaphores[lane_name]
    wait_start = time.monotonic()
    await semaphore.acquire()
    queue_wait_ms = (time.monotonic() - wait_start) * 1000
    trace = telemetry.start_ai_trace(
        feature=feature,
        route_type=route_type,
        lane=lane_name,
        input_size=input_size,
        cache_status=cache_status,
        queue_wait_ms=queue_wait_ms,
    )
    _active[lane_name] += 1
    status = "ok"
    error = ""
    try:
        async with asyncio.timeout(_LANE_TIMEOUT_SECONDS[lane_name]):
            async for token in generator_factory():
                trace.mark_first_event()
                trace.add_output(token)
                yield token
        _completed[lane_name] += 1
    except TimeoutError as exc:
        status = "timeout"
        error = f"{lane_name} request timed out"
        _timed_out[lane_name] += 1
        logger.warning("{} feature={} route={} timed out", error, feature, route_type)
        raise RuntimeError(error) from exc
    except asyncio.CancelledError:
        status = "cancelled"
        error = "request cancelled"
        logger.info("AI request cancelled feature={} route={} lane={}", feature, route_type, lane_name)
        raise
    except Exception as exc:
        status = "error"
        error = repr(exc)
        _failed[lane_name] += 1
        raise
    finally:
        _active[lane_name] -= 1
        semaphore.release()
        trace.finish(status=status, error=error)


async def run_with_lane(
    *,
    lane: str | None,
    feature: str,
    route_type: str,
    input_size: int,
    cache_status: str,
    call_factory: Callable[[], Awaitable[T]],
) -> T:
    lane_name = normalize_lane(lane, route_type)
    semaphore = _semaphores[lane_name]
    wait_start = time.monotonic()
    await semaphore.acquire()
    queue_wait_ms = (time.monotonic() - wait_start) * 1000
    trace = telemetry.start_ai_trace(
        feature=feature,
        route_type=route_type,
        lane=lane_name,
        input_size=input_size,
        cache_status=cache_status,
        queue_wait_ms=queue_wait_ms,
    )
    _active[lane_name] += 1
    status = "ok"
    error = ""
    try:
        async with asyncio.timeout(_LANE_TIMEOUT_SECONDS[lane_name]):
            result = await call_factory()
        trace.mark_first_event()
        trace.add_output(result)
        _completed[lane_name] += 1
        return result
    except TimeoutError as exc:
        status = "timeout"
        error = f"{lane_name} request timed out"
        _timed_out[lane_name] += 1
        logger.warning("{} feature={} route={} timed out", error, feature, route_type)
        raise RuntimeError(error) from exc
    except asyncio.CancelledError:
        status = "cancelled"
        error = "request cancelled"
        logger.info("AI request cancelled feature={} route={} lane={}", feature, route_type, lane_name)
        raise
    except Exception as exc:
        status = "error"
        error = repr(exc)
        _failed[lane_name] += 1
        raise
    finally:
        _active[lane_name] -= 1
        semaphore.release()
        trace.finish(status=status, error=error)


def get_scheduler_status() -> dict[str, Any]:
    def queued_count(semaphore: asyncio.Semaphore) -> int:
        waiters = getattr(semaphore, "_waiters", None)
        return len(waiters) if waiters else 0

    return {
        "lanes": {
            lane: {
                "limit": _LANE_LIMITS[lane],
                "active": max(0, _active[lane]),
                "queued_estimate": queued_count(_semaphores[lane]),
                "timeout_seconds": _LANE_TIMEOUT_SECONDS[lane],
                "completed": _completed[lane],
                "timed_out": _timed_out[lane],
                "failed": _failed[lane],
            }
            for lane in _LANE_LIMITS
        }
    }
