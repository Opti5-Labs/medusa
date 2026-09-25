"""
FastAPI application entry point.

Routers are registered here. CORS is enabled for the frontend origin read from
the ALLOWED_ORIGIN environment variable (see app/config.py).

All routes are stub implementations — real logic lives in app/api/*.
"""

import time
import uuid

from fastapi import FastAPI, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app.config import ALLOWED_ORIGIN
from app.models.contracts import (
    DebugSession,
    FixAttempt,
    Issue,
    LogEvent,
    ReproAttempt,
    Recommendation,
    ScanResult,
)

app = FastAPI(title="Medusa", version="0.1.0")

# ── CORS ───────────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Health ─────────────────────────────────────────────────────────────────────


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True}


# ── Scan ───────────────────────────────────────────────────────────────────────


@app.post("/api/scan", response_model=ScanResult)
async def scan(body: dict) -> ScanResult:
    """
    Stub — Person 2 implements the real scan pipeline.

    Accepts: {"source": "demo" | "github", "repo_url"?: str}
    Returns: ScanResult with placeholder data.
    """
    return ScanResult(
        scan_id=str(uuid.uuid4()),
        repo_source=body.get("source", "demo"),
        language="python",
        files_scanned=[],
        files_total=0,
        issues=[
            Issue(
                id=str(uuid.uuid4()),
                title="Placeholder issue",
                description="Stub scan result — real pipeline not yet implemented.",
                priority="Medium",
                source="scan",
            )
        ],
        warnings=["Stub response — scan pipeline not implemented yet."],
    )


@app.post("/api/scan/upload", response_model=ScanResult)
async def scan_upload(file: UploadFile) -> ScanResult:
    """
    Stub — Person 2 implements zip ingest and scan.

    Accepts: multipart form with a 'file' field (zip).
    Returns: ScanResult with placeholder data.
    """
    return ScanResult(
        scan_id=str(uuid.uuid4()),
        repo_source="zip",
        language="unknown",
        files_scanned=[],
        files_total=0,
        issues=[],
        warnings=["Stub response — zip scan pipeline not implemented yet."],
    )


# ── Reproduce ─────────────────────────────────────────────────────────────────


@app.post("/api/issues/{issue_id}/repro", response_model=ReproAttempt)
async def start_repro(issue_id: str) -> ReproAttempt:
    """
    Stub — Person 1 (sandbox) and Person 2 (reasoning) implement the real pipeline.

    Returns a ReproAttempt with status="running".
    The client then opens GET /api/repro/{attempt_id}/events to stream progress.
    """
    return ReproAttempt(
        attempt_id=str(uuid.uuid4()),
        issue_id=issue_id,
        mode="reasoning",
        status="running",
        log=[],
    )


@app.get("/api/repro/{attempt_id}/events")
async def repro_events(attempt_id: str) -> StreamingResponse:
    """
    Stub SSE stream for a ReproAttempt.

    Emits a few placeholder LogEvent lines then a 'done' event
    carrying the final ReproAttempt JSON.
    Person 1/2 replaces this with real log streaming.
    """

    async def event_stream():
        stub_events = [
            LogEvent(ts=time.time(), source="stub", level="info", message="Repro stub started."),
            LogEvent(ts=time.time(), source="stub", level="info", message="(placeholder — real pipeline not implemented)"),
            LogEvent(ts=time.time(), source="stub", level="result", message="Stub complete."),
        ]
        for event in stub_events:
            yield f"event: log\ndata: {event.model_dump_json()}\n\n"

        final = ReproAttempt(
            attempt_id=attempt_id,
            issue_id="unknown",
            mode="reasoning",
            status="plausible",
            log=stub_events,
            confidence=0.0,
        )
        yield f"event: done\ndata: {final.model_dump_json()}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ── Debug ─────────────────────────────────────────────────────────────────────


@app.post("/api/issues/{issue_id}/debug", response_model=DebugSession)
async def start_debug(issue_id: str, body: dict) -> DebugSession:
    """
    Stub — Person 1 (sandbox) and Person 2 (reasoning) implement the real pipeline.

    Accepts: {"candidates": 2..6}
    Returns a DebugSession with status="running" candidates.
    """
    n = int(body.get("candidates", 2))
    candidates = [
        FixAttempt(
            candidate_id=f"c{i + 1}",
            approach=f"Stub candidate {i + 1}",
            sandbox_status="not_applicable",
            active=True,
        )
        for i in range(n)
    ]
    return DebugSession(
        session_id=str(uuid.uuid4()),
        issue_id=issue_id,
        mode="reasoning",
        candidates=candidates,
    )


@app.get("/api/debug/{session_id}/events")
async def debug_events(session_id: str) -> StreamingResponse:
    """
    Stub SSE stream for a DebugSession.

    Emits placeholder LogEvents then a 'done' event with the final
    DebugSession + Recommendation JSON.
    Person 1/2 replaces this with real candidate log streaming.
    """

    async def event_stream():
        stub_events = [
            LogEvent(ts=time.time(), source="candidate:c1", level="info", message="Debug stub started."),
            LogEvent(ts=time.time(), source="candidate:c1", level="result", message="Stub complete."),
        ]
        for event in stub_events:
            yield f"event: log\ndata: {event.model_dump_json()}\n\n"

        session = DebugSession(
            session_id=session_id,
            issue_id="unknown",
            mode="reasoning",
            candidates=[
                FixAttempt(
                    candidate_id="c1",
                    approach="Stub candidate 1",
                    sandbox_status="not_applicable",
                    active=True,
                )
            ],
        )
        recommendation = Recommendation(
            candidate_id="c1",
            reason="Stub recommendation — debug pipeline not implemented yet.",
        )
        payload = {
            "session": session.model_dump(),
            "recommendation": recommendation.model_dump(),
        }
        import json
        yield f"event: done\ndata: {json.dumps(payload)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/debug/{session_id}/download")
async def debug_download(session_id: str, candidate_id: str):
    """
    Stub — returns a plain 501 until Person 2 implements zip assembly.
    """
    from fastapi import HTTPException
    raise HTTPException(status_code=501, detail="Download not implemented yet.")
