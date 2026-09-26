"""
Architecture generation API.

    POST /api/scans/{scan_id}/architecture           -> ArchitectureReport (status "running", or the
                                                          existing report if already requested for this scan)
    GET  /api/architecture/{architecture_id}/events   -> SSE: log events, then done (ArchitectureReport)
    GET  /api/architecture/{architecture_id}          -> ArchitectureReport (reconnect / no-SSE fallback)
    GET  /api/architecture/{architecture_id}/download?format=mermaid|json

Starting a run is rate limited per IP, like reproduce/debug. A repeated
request for the same scan reuses the running or finished report instead of
starting a second pipeline (ScanRecord.architecture_id memoises it) — no
rate-limit cost and no rework.
"""

from collections.abc import AsyncIterator
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import Response
from sse_starlette.sse import EventSourceResponse

from app import config
from app.errors import MedusaError
from app.models.contracts import ArchitectureReport
from app.pipelines.architecture import start_architecture
from app.ratelimit import RateLimiter
from app.store import RunStore, StoreFullError
from app.streaming import EventChannel

router = APIRouter()

_arch_limiter = RateLimiter(
    max_calls=config.RATE_ARCH_PER_WINDOW,
    window_seconds=config.RATE_WINDOW_SECONDS,
    what="architecture reports",
)

_store: RunStore | None = None


def set_store(store: RunStore) -> None:
    global _store
    _store = store


def _get_store() -> RunStore:
    assert _store is not None, "RunStore not initialised"
    return _store


def _sse(channel: EventChannel) -> EventSourceResponse:
    async def events() -> AsyncIterator[dict]:
        async for name, data in channel.stream():
            yield {"event": name, "data": data}

    return EventSourceResponse(events(), ping=15, send_timeout=30)


@router.post("/api/scans/{scan_id}/architecture", response_model=ArchitectureReport)
async def create_architecture(scan_id: str, request: Request) -> ArchitectureReport:
    store = _get_store()
    record = await store.get(scan_id)
    if record is None:
        raise MedusaError(
            404,
            "Scan not found or expired. Scans are kept for 30 minutes — "
            "please run the scan again.",
        )

    if record.architecture_id:
        existing = store.arch_runs.get(record.architecture_id)
        if existing is not None:
            return existing.report
        # The run was swept (TTL) even though the scan record survived; start fresh.
        record.architecture_id = None

    _arch_limiter.check(request)
    try:
        run = await start_architecture(store, record)
    except StoreFullError as exc:
        raise MedusaError(503, str(exc)) from exc
    return run.report


@router.get("/api/architecture/{architecture_id}/events")
async def architecture_events(architecture_id: str) -> EventSourceResponse:
    run = _get_store().arch_runs.get(architecture_id)
    if run is None:
        raise MedusaError(404, "Architecture report not found or expired.")
    return _sse(run.channel)


@router.get("/api/architecture/{architecture_id}", response_model=ArchitectureReport)
async def get_architecture(architecture_id: str) -> ArchitectureReport:
    run = _get_store().arch_runs.get(architecture_id)
    if run is None:
        raise MedusaError(404, "Architecture report not found or expired.")
    return run.report


@router.get("/api/architecture/{architecture_id}/download")
async def download_architecture(
    architecture_id: str, format: Literal["mermaid", "json"]
) -> Response:
    run = _get_store().arch_runs.get(architecture_id)
    if run is None:
        raise MedusaError(404, "Architecture report not found or expired.")
    if run.report.status == "running":
        raise MedusaError(
            409,
            "The architecture report is still being generated. Please wait for it to finish.",
        )

    if format == "mermaid":
        if not run.report.mermaid:
            raise MedusaError(
                409,
                "No diagram was produced for this repository. See the architecture report for why.",
            )
        return Response(content=run.report.mermaid, media_type="text/plain")

    return Response(
        content=run.report.model_dump_json(indent=2),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="architecture-{architecture_id}.json"'
        },
    )
