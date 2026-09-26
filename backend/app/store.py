"""
In-memory run store with TTL sweeper and temp-dir lifecycle.

Holds three kinds of record, all deleted after RUN_TTL_SECONDS:
    scans        scan_id    -> ScanRecord  (result, extracted tree, issue index)
    repro runs   attempt_id -> ReproRun
    debug runs   session_id -> DebugRun

Usage:
    store = RunStore()
    # register in FastAPI lifespan (see main.py)

    record = await store.create(scan_id, tmp_dir, result, root=..., scenarios=...)
    found = await store.find_issue(issue_id)   # (ScanRecord, Issue) | None
"""

import asyncio
import logging
import shutil
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app import config
from app.config import RUN_TTL_SECONDS
from app.models.contracts import (
    AskAnswer,
    DebugSession,
    Issue,
    Recommendation,
    ReproAttempt,
    ScanResult,
)
from app.streaming import EventChannel

log = logging.getLogger(__name__)

# Hard cap on scans that own temp files, to prevent disk exhaustion.
# Exceeding this returns a 503 rather than filling the disk.
_MAX_STORED_SCANS = 200
# Demo scans own no files; the oldest are evicted past this count instead.
_MAX_DEMO_SCANS = 1000
# Cap on live repro/debug runs across all users.
_MAX_RUNS = 200


class StoreFullError(RuntimeError):
    """The store is at capacity. The message is safe to show users."""


@dataclass
class ScanRecord:
    scan_id: str
    tmp_dir: Path | None  # the whole temp dir; None for demo (bundled)
    result: ScanResult
    root: Path | None = None  # extracted repo root, read as text only
    scenarios: dict[str, str] = field(default_factory=dict)  # issue_id -> demo scenario
    history: list[tuple[str, str]] = field(default_factory=list)  # (question, answer)
    created_at: float = field(default_factory=time.monotonic)

    def is_expired(self) -> bool:
        return (time.monotonic() - self.created_at) > RUN_TTL_SECONDS

    def remember(self, question: str, answer: str) -> None:
        self.history.append((question, answer))
        del self.history[: -config.ASK_MAX_REMEMBERED_TURNS]


# Kept for callers/tests that use the old name.
RunRecord = ScanRecord


@dataclass
class ReproRun:
    attempt: ReproAttempt
    channel: EventChannel
    baseline: Any = None  # SandboxResult on the original code (sandboxed mode)
    evidence: str | None = None  # sandbox runtime evidence given to investigators
    investigations: list = field(default_factory=list)  # InvestigatorResult per agent
    task: asyncio.Task | None = None
    created_at: float = field(default_factory=time.monotonic)


@dataclass
class DebugRun:
    session: DebugSession
    channel: EventChannel
    workdirs: dict[str, Path] = field(
        default_factory=dict
    )  # candidate_id -> patched tree
    tmp_dir: Path | None = None
    recommendation: Recommendation | None = None
    task: asyncio.Task | None = None
    created_at: float = field(default_factory=time.monotonic)


@dataclass
class AskRun:
    ask_id: str
    scan_id: str
    channel: EventChannel
    answer: AskAnswer | None = None
    task: asyncio.Task | None = None
    created_at: float = field(default_factory=time.monotonic)


class RunStore:
    def __init__(self) -> None:
        self._records: OrderedDict[str, ScanRecord] = OrderedDict()
        self._issue_index: dict[str, str] = {}  # issue_id -> scan_id
        self.repro_runs: dict[str, ReproRun] = {}
        self.debug_runs: dict[str, DebugRun] = {}
        self.ask_runs: dict[str, AskRun] = {}
        self._lock = asyncio.Lock()
        self._sweeper_task: asyncio.Task | None = None

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background TTL sweeper. Call from FastAPI lifespan."""
        self._sweeper_task = asyncio.create_task(self._sweep_loop())

    async def stop(self) -> None:
        """Cancel the sweeper and running pipelines. Call from FastAPI lifespan."""
        if self._sweeper_task:
            self._sweeper_task.cancel()
            try:
                await self._sweeper_task
            except asyncio.CancelledError:
                pass
        for run in [
            *self.repro_runs.values(),
            *self.debug_runs.values(),
            *self.ask_runs.values(),
        ]:
            if run.task and not run.task.done():
                run.task.cancel()

    # ── Scans ──────────────────────────────────────────────────────────────────

    async def create(
        self,
        scan_id: str,
        tmp_dir: Path | None,
        result: ScanResult,
        *,
        root: Path | None = None,
        scenarios: dict[str, str] | None = None,
    ) -> ScanRecord:
        """
        Register a completed scan. *tmp_dir* is the entire temp directory
        (returned by ingest_zip / ingest_github); it is deleted on expiry
        or explicit delete(). Pass None for the demo scan (no temp files).

        Raises StoreFullError if too many file-backed scans are stored.
        """
        evicted: list[str] = []
        async with self._lock:
            if tmp_dir is not None:
                owned = sum(1 for r in self._records.values() if r.tmp_dir is not None)
                if owned >= _MAX_STORED_SCANS:
                    if tmp_dir.exists():
                        _remove_dir(tmp_dir)
                    raise StoreFullError(
                        "Server is busy with too many active scans. Please try again shortly."
                    )
            else:
                demos = [sid for sid, r in self._records.items() if r.tmp_dir is None]
                evicted = demos[: max(0, len(demos) - _MAX_DEMO_SCANS + 1)]
            record = ScanRecord(
                scan_id=scan_id,
                tmp_dir=tmp_dir,
                result=result,
                root=root,
                scenarios=dict(scenarios or {}),
            )
            self._records[scan_id] = record
            for issue in result.issues:
                self._issue_index[issue.id] = scan_id
        for sid in evicted:
            await self.delete(sid)
        return record

    async def get(self, scan_id: str) -> ScanRecord | None:
        async with self._lock:
            record = self._records.get(scan_id)
        if record is None or record.is_expired():
            if record is not None:
                await self.delete(scan_id)
            return None
        return record

    async def find_issue(self, issue_id: str) -> tuple[ScanRecord, Issue] | None:
        async with self._lock:
            scan_id = self._issue_index.get(issue_id)
        if scan_id is None:
            return None
        record = await self.get(scan_id)
        if record is None:
            return None
        for issue in record.result.issues:
            if issue.id == issue_id:
                return record, issue
        return None

    async def delete(self, scan_id: str) -> None:
        async with self._lock:
            record = self._records.pop(scan_id, None)
            if record:
                for issue in record.result.issues:
                    self._issue_index.pop(issue.id, None)
        if record and record.tmp_dir and record.tmp_dir.exists():
            _remove_dir(record.tmp_dir)

    # ── Runs ───────────────────────────────────────────────────────────────────

    def _check_run_capacity(self) -> None:
        if len(self.repro_runs) + len(self.debug_runs) >= _MAX_RUNS:
            raise StoreFullError(
                "Server is busy with too many active runs. Please try again shortly."
            )

    def add_repro(self, run: ReproRun) -> None:
        self._check_run_capacity()
        self.repro_runs[run.attempt.attempt_id] = run

    def add_debug(self, run: DebugRun) -> None:
        self._check_run_capacity()
        self.debug_runs[run.session.session_id] = run

    def latest_repro_for(self, issue_id: str) -> ReproRun | None:
        runs = [r for r in self.repro_runs.values() if r.attempt.issue_id == issue_id]
        return max(runs, key=lambda r: r.created_at) if runs else None

    def add_ask(self, run: AskRun) -> None:
        # a finished answer only needs to stay long enough for a browser to replay it
        stale = time.monotonic() - config.ASK_FINISHED_KEEP_SECONDS
        for ask_id in [
            k
            for k, r in self.ask_runs.items()
            if r.channel.closed and r.created_at < stale
        ]:
            del self.ask_runs[ask_id]
        if len(self.ask_runs) >= config.ASK_MAX_RUNS:
            raise StoreFullError(
                "Server is busy answering too many questions. Please try again shortly."
            )
        self.ask_runs[run.ask_id] = run

    def ask_running_for(self, scan_id: str) -> bool:
        return any(
            r.scan_id == scan_id and not r.channel.closed
            for r in self.ask_runs.values()
        )

    def latest_debug_for(self, issue_id: str) -> DebugRun | None:
        runs = [r for r in self.debug_runs.values() if r.session.issue_id == issue_id]
        return max(runs, key=lambda r: r.created_at) if runs else None

    def _drop_run(self, run: ReproRun | DebugRun | AskRun) -> None:
        if run.task and not run.task.done():
            run.task.cancel()
        if isinstance(run, DebugRun) and run.tmp_dir and run.tmp_dir.exists():
            _remove_dir(run.tmp_dir)

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

        cutoff = time.monotonic() - RUN_TTL_SECONDS
        for runs in (self.repro_runs, self.debug_runs, self.ask_runs):
            for run_id in [k for k, r in runs.items() if r.created_at < cutoff]:
                self._drop_run(runs.pop(run_id))


def _remove_dir(path: Path) -> None:
    try:
        shutil.rmtree(path, ignore_errors=True)
    except OSError as exc:  # pragma: no cover
        log.warning("store: failed to remove %s: %s", path, exc)
