"""
Bob golden-run replay for the OptiLearn demo (BOB_MODE=replay).

Replays golden/optilearn/investigation.jsonl as LogEvents with realistic pacing.
The file must start with a {"recorded": true, ...} header; see golden/optilearn/README.md.
Returns None when no recorded run exists, so callers fall back to Granite.
"""

import asyncio
import json
import logging
from dataclasses import dataclass

from app import config
from app.streaming import EventChannel

log = logging.getLogger(__name__)

GOLDEN_FILE = "investigation.jsonl"
_ALLOWED_SOURCES = {
    "investigator:runtime",
    "investigator:repository",
    "investigator:skeptic",
    "synthesis",
}


@dataclass
class ReplayedSynthesis:
    root_cause: str
    confidence: float | None


def golden_available() -> bool:
    path = config.GOLDEN_DIR / GOLDEN_FILE
    if config.BOB_MODE != "replay" or not path.is_file():
        return False
    try:
        with open(path, encoding="utf-8") as fh:
            header = json.loads(fh.readline())
        return header.get("recorded") is True
    except (OSError, ValueError):
        return False


async def replay_investigation(channel: EventChannel) -> ReplayedSynthesis | None:
    """Stream the recorded investigation into *channel*. None if unavailable."""
    if not golden_available():
        return None
    lines = (config.GOLDEN_DIR / GOLDEN_FILE).read_text("utf-8").splitlines()[1:]
    synthesis: ReplayedSynthesis | None = None
    for raw in lines:
        if not raw.strip():
            continue
        item = json.loads(raw)
        if "synthesis" in item:
            s = item["synthesis"]
            synthesis = ReplayedSynthesis(
                root_cause=str(s["root_cause"]), confidence=s.get("confidence")
            )
            continue
        source = item.get("source")
        if source not in _ALLOWED_SOURCES:
            log.warning("bob replay: skipping event with source %r", source)
            continue
        level = item.get("level", "info")
        await channel.emit(
            source,
            level if level in ("info", "warn", "result") else "info",
            str(item["message"]),
        )
        await asyncio.sleep(config.BOB_REPLAY_DELAY_S)
    return synthesis
