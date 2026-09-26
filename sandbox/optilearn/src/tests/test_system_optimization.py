import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import generated_cache, model_scheduler


async def _collect(stream):
    items = []
    async for item in stream:
        items.append(item)
    return items


@pytest.mark.asyncio
async def test_scheduler_keeps_background_from_blocking_live_lane():
    async def slow_background():
        await asyncio.sleep(0.08)
        yield "background"

    async def quick_live():
        yield "live"

    background_task = asyncio.create_task(
        _collect(
            model_scheduler.stream_with_lane(
                lane="background",
                feature="test.background",
                route_type="TRANSLATION",
                input_size=1,
                cache_status="miss",
                generator_factory=slow_background,
            )
        )
    )
    await asyncio.sleep(0.01)

    start = asyncio.get_event_loop().time()
    live_result = await _collect(
        model_scheduler.stream_with_lane(
            lane="live_translation",
            feature="test.live",
            route_type="TRANSLATION",
            input_size=1,
            cache_status="miss",
            generator_factory=quick_live,
        )
    )
    elapsed = asyncio.get_event_loop().time() - start

    assert live_result == ["live"]
    assert elapsed < 0.05
    assert await background_task == ["background"]


def test_generated_cache_key_is_stable_and_input_sensitive():
    payload = {
        "material_id": "m1",
        "language": "fr",
        "grade": "3rd",
        "source_hash": generated_cache.hash_text("source text"),
    }
    first = generated_cache.make_cache_key("material.translation", payload, "v1")
    second = generated_cache.make_cache_key("material.translation", dict(reversed(payload.items())), "v1")
    changed = generated_cache.make_cache_key(
        "material.translation",
        {**payload, "language": "ar"},
        "v1",
    )

    assert first == second
    assert first != changed
