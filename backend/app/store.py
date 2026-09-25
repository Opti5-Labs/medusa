"""
In-memory run store with TTL sweeper and temp-dir lifecycle.

RunStore maps scan_id -> RunRecord.
A background asyncio task sweeps expired records every minute and
deletes their temp directories.

Usage:
    store = RunStore()
    # register in FastAPI lifespan (see main.py)

    record = store.create(scan_id, tmp_dir, result)
    record = store.get(scan_id)          # None if missing / expired
    store.delete(scan_id)               # also removes the whole tmp_dir
"""

import asyncio
import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.config import RUN_TTL_SECONDS
from app.models.contracts import ScanResult

log = logging.getLogger(__name__)

# Hard cap on in-flight scan records to prevent disk exhaustion.
# Exceeding this returns a 503 rather than filling the disk.
_MAX_STORED_SCANS = 200


@dataclass
class RunRecord:
    scan_id: str
    tmp_dir: Path | None  # the whole temp dir; None for demo (bundled)
    result: ScanResult
    created_at: float = field(default_factory=time.monotonic)

    def is_expired(self) -> bool:
        return (time.monotonic() - self.created_at) > RUN_TTL_SECONDS


class RunStore:
    def __init__(self) -> None:
        self._records: dict[str, RunRecord] = {}
        self._lock = asyncio.Lock()
        self._sweeper_task: asyncio.Task | None = None

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background TTL sweeper. Call from FastAPI lifespan."""
        self._sweeper_task = asyncio.create_task(self._sweep_loop())

    async def stop(self) -> None:
        """Cancel the sweeper and wait for it to finish. Call from FastAPI lifespan."""
        if self._sweeper_task:
            self._sweeper_task.cancel()
            try:
                await self._sweeper_task
            except asyncio.CancelledError:
                pass

    # ── CRUD ───────────────────────────────────────────────────────────────────

    async def create(
        self, scan_id: str, tmp_dir: Path | None, result: ScanResult
    ) -> RunRecord:
        """
        Register a completed scan.  *tmp_dir* is the entire temp directory
        (returned by ingest_zip / ingest_github); it will be deleted on expiry
        or explicit delete().  Pass None for the demo scan (no temp files).

        Raises RuntimeError with status hint 503 if the store is full.
        """
        async with self._lock:
            if len(self._records) >= _MAX_STORED_SCANS:
                # Clean up the caller's temp dir so it doesn't leak
                if tmp_dir and tmp_dir.exists():
                    _remove_dir(tmp_dir)
                raise RuntimeError(
                    "Server is busy with too many active scans. Please try again shortly."
                )
            record = RunRecord(scan_id=scan_id, tmp_dir=tmp_dir, result=result)
            self._records[scan_id] = record
        return record

    async def get(self, scan_id: str) -> RunRecord | None:
        async with self._lock:
            record = self._records.get(scan_id)
        if record is None or record.is_expired():
            if record is not None:
                await self.delete(scan_id)
            return None
        return record

    async def delete(self, scan_id: str) -> None:
        async with self._lock:
            record = self._records.pop(scan_id, None)
        if record and record.tmp_dir and record.tmp_dir.exists():
            _remove_dir(record.tmp_dir)

    # ── Sweeper ────────────────────────────────────────────────────────────────

    async def _sweep_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            try:
                await self._sweep_once()
            except Exception:
                log.exception("store: sweep_once raised unexpectedly; continuing")

    async def _sweep_once(self) -> None:
        async with self._lock:
            expired = [sid for sid, r in self._records.items() if r.is_expired()]
        for sid in expired:
            log.info("store: TTL expired, deleting scan_id=%s", sid)
            await self.delete(sid)


def _remove_dir(path: Path) -> None:
    try:
        shutil.rmtree(path, ignore_errors=True)
    except OSError as exc:  # pragma: no cover
        log.warning("store: failed to remove %s: %s", path, exc)
