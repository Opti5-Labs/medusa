"""
Per-run event channel feeding the SSE endpoints.

A pipeline emits events into its channel; any number of SSE clients replay the
history from the start and then follow live events until the final `done`.
Replaying from the start means a reconnecting browser never misses events.
"""

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Literal

from app.models.contracts import LogEvent

Level = Literal["info", "warn", "error", "result"]


class EventChannel:
    def __init__(self) -> None:
        self._events: list[tuple[str, str]] = []  # (event name, JSON data)
        self._cond = asyncio.Condition()
        self.log: list[LogEvent] = []
        self.closed = False

    async def _push(self, name: str, data: str) -> None:
        async with self._cond:
            self._events.append((name, data))
            self._cond.notify_all()

    async def emit(self, source: str, level: Level, message: str) -> LogEvent:
        event = LogEvent(ts=time.time(), source=source, level=level, message=message)
        self.log.append(event)
        await self._push("log", event.model_dump_json())
        return event

    async def done(self, payload_json: str) -> None:
        async with self._cond:
            self._events.append(("done", payload_json))
            self.closed = True
            self._cond.notify_all()

    async def stream(self) -> AsyncIterator[tuple[str, str]]:
        index = 0
        while True:
            async with self._cond:
                await self._cond.wait_for(
                    lambda i=index: i < len(self._events) or self.closed
                )
                batch = self._events[index:]
                index = len(self._events)
                finished = self.closed
            for item in batch:
                yield item
            if finished and index >= len(self._events):
                return
