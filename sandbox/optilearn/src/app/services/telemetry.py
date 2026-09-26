"""Lightweight AI workflow telemetry for OptiLearn.

This module records system-layer timing only. It does not inspect or mutate
model request parameters.
"""
from __future__ import annotations

import json
import time
import uuid
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

_RECENT_LIMIT = 300
_recent_events: deque[dict[str, Any]] = deque(maxlen=_RECENT_LIMIT)
_active_traces: dict[str, "AiTrace"] = {}
_counters: Counter[str] = Counter()


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class AiTrace:
    feature: str
    route_type: str
    lane: str
    input_size: int = 0
    cache_status: str = "miss"
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    started_at: float = field(default_factory=time.monotonic)
    wall_started_ms: int = field(default_factory=_now_ms)
    queue_wait_ms: float = 0.0
    first_event_ms: float | None = None
    output_size: int = 0
    status: str = "running"
    error: str = ""
    fallback_reason: str = ""
    raw_timing: dict[str, Any] = field(default_factory=dict)

    def mark_first_event(self) -> None:
        if self.first_event_ms is None:
            self.first_event_ms = (time.monotonic() - self.started_at) * 1000

    def add_output(self, value: Any) -> None:
        self.output_size += len(str(value or ""))

    def finish(
        self,
        *,
        status: str = "ok",
        error: str = "",
        fallback_reason: str = "",
        raw_timing: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.status = status
        self.error = error
        self.fallback_reason = fallback_reason
        if raw_timing:
            self.raw_timing.update(raw_timing)
        event = {
            "trace_id": self.trace_id,
            "feature": self.feature,
            "route_type": self.route_type,
            "lane": self.lane,
            "status": self.status,
            "cache_status": self.cache_status,
            "queue_wait_ms": round(self.queue_wait_ms, 2),
            "time_to_first_event_ms": (
                round(self.first_event_ms, 2)
                if self.first_event_ms is not None
                else None
            ),
            "total_latency_ms": round((time.monotonic() - self.started_at) * 1000, 2),
            "input_size": self.input_size,
            "output_size": self.output_size,
            "error": self.error,
            "fallback_reason": self.fallback_reason,
            "raw_timing": self.raw_timing,
            "started_at_ms": self.wall_started_ms,
            "finished_at_ms": _now_ms(),
        }
        _recent_events.appendleft(event)
        _active_traces.pop(self.trace_id, None)
        _counters[f"{self.feature}:{self.status}"] += 1
        _counters[f"lane:{self.lane}:{self.status}"] += 1
        logger.info("ai_metric {}", json.dumps(event, ensure_ascii=False, sort_keys=True))
        return event


def start_ai_trace(
    *,
    feature: str,
    route_type: str,
    lane: str,
    input_size: int = 0,
    cache_status: str = "miss",
    queue_wait_ms: float = 0.0,
) -> AiTrace:
    trace = AiTrace(
        feature=feature,
        route_type=route_type,
        lane=lane,
        input_size=input_size,
        cache_status=cache_status,
        queue_wait_ms=queue_wait_ms,
    )
    _active_traces[trace.trace_id] = trace
    return trace


def record_cache_event(
    *,
    feature: str,
    cache_status: str,
    input_size: int = 0,
    output_size: int = 0,
) -> None:
    trace = start_ai_trace(
        feature=feature,
        route_type="CACHE",
        lane="cache",
        input_size=input_size,
        cache_status=cache_status,
    )
    trace.output_size = output_size
    trace.mark_first_event()
    trace.finish(status="ok")


def recent_ai_events(limit: int = 100) -> list[dict[str, Any]]:
    return list(_recent_events)[: max(0, min(limit, _RECENT_LIMIT))]


def active_ai_traces() -> list[dict[str, Any]]:
    now = time.monotonic()
    return [
        {
            "trace_id": trace.trace_id,
            "feature": trace.feature,
            "route_type": trace.route_type,
            "lane": trace.lane,
            "cache_status": trace.cache_status,
            "age_ms": round((now - trace.started_at) * 1000, 2),
            "queue_wait_ms": round(trace.queue_wait_ms, 2),
            "input_size": trace.input_size,
            "output_size": trace.output_size,
        }
        for trace in _active_traces.values()
    ]


def telemetry_summary() -> dict[str, Any]:
    events = list(_recent_events)
    by_feature = Counter(event["feature"] for event in events)
    by_lane = Counter(event["lane"] for event in events)
    errors = [event for event in events if event.get("status") != "ok"]
    latencies = [event["total_latency_ms"] for event in events if event.get("total_latency_ms") is not None]
    return {
        "recent_count": len(events),
        "active_count": len(_active_traces),
        "by_feature": dict(by_feature),
        "by_lane": dict(by_lane),
        "errors": errors[:20],
        "p50_latency_ms": _percentile(latencies, 50),
        "p95_latency_ms": _percentile(latencies, 95),
        "counters": dict(_counters),
    }


def _percentile(values: list[float], percentile: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((percentile / 100) * (len(ordered) - 1))))
    return round(ordered[index], 2)
