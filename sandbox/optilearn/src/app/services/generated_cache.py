"""Persistent cache helpers for generated educational artifacts."""
from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from typing import Any

from app.services import db, telemetry

# ── In-memory L1 cache (avoids DB round-trip on repeated cache hits) ─────────
# Translations and explanations are large; cap at 100 entries (~50-100 MB max).
_L1_MAX = 100
_l1_cache: OrderedDict[str, str] = OrderedDict()


def _l1_get(key: str) -> str | None:
    if key in _l1_cache:
        _l1_cache.move_to_end(key)
        return _l1_cache[key]
    return None


def _l1_set(key: str, value: str) -> None:
    _l1_cache[key] = value
    _l1_cache.move_to_end(key)
    if len(_l1_cache) > _L1_MAX:
        _l1_cache.popitem(last=False)


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def hash_text(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def make_cache_key(feature: str, payload: dict[str, Any], prompt_version: str = "v1") -> str:
    source = stable_json({"feature": feature, "prompt_version": prompt_version, **payload})
    digest = hash_text(source)
    return f"{feature}:{prompt_version}:{digest}"


async def get_text(cache_key: str, feature: str = "") -> str | None:
    hit = _l1_get(cache_key)
    if hit is not None:
        telemetry.record_cache_event(
            feature=feature or "generated_cache",
            cache_status="hit",
            output_size=len(hit),
        )
        return hit
    row = await db.get_generated_cache(cache_key)
    if not row:
        telemetry.record_cache_event(feature=feature or "generated_cache", cache_status="miss")
        return None
    value = row.get("payload") or ""
    _l1_set(cache_key, value)
    telemetry.record_cache_event(
        feature=feature or row.get("feature") or "generated_cache",
        cache_status="hit",
        output_size=len(value),
    )
    return value


async def set_text(
    cache_key: str,
    feature: str,
    value: str,
    *,
    input_hash: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    _l1_set(cache_key, value)
    await db.set_generated_cache(
        cache_key=cache_key,
        feature=feature,
        payload=value,
        input_hash=input_hash,
        metadata=metadata or {},
    )


async def get_json(cache_key: str, feature: str = "") -> Any | None:
    text = await get_text(cache_key, feature)
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


async def set_json(
    cache_key: str,
    feature: str,
    value: Any,
    *,
    input_hash: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    await set_text(
        cache_key,
        feature,
        stable_json(value),
        input_hash=input_hash,
        metadata=metadata,
    )
