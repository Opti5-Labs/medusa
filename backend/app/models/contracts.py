from typing import Literal

from pydantic import BaseModel

# ── Shared literals ────────────────────────────────────────────────────────────

Priority = Literal["Low", "Medium", "High"]
Mode = Literal["sandboxed", "reasoning"]

# ── Core models ────────────────────────────────────────────────────────────────


class Issue(BaseModel):
    id: str  # uuid, unique across all scans
    title: str
    description: str
    priority: Priority
    source: Literal["scan", "github_issue"]
    category: (
        Literal["security", "correctness", "performance", "maintainability"] | None
    ) = None
    file: str | None = None
    function: str | None = None
    github_url: str | None = None


class ScanResult(BaseModel):
    scan_id: str
    repo_source: Literal["demo", "github", "zip"]
    language: str
    files_scanned: list[str]  # paths actually analysed, shown to the user
    files_total: int
    issues: list[Issue]
    warnings: list[str] = []  # e.g. "scan capped at 40 files"


class LogEvent(BaseModel):
    ts: float
    source: str  # "investigator:1", "synthesis", "sandbox", "candidate:c2", "granite"
    level: Literal["info", "warn", "error", "result"]
    message: str


class ReproAttempt(BaseModel):
    attempt_id: str
    issue_id: str
    mode: Mode
    status: Literal["running", "reproduced", "not_reproducible", "plausible", "error"]
    log: list[LogEvent] = []
    root_cause: str | None = None
    confidence: float | None = None  # reasoning mode only


class FixAttempt(BaseModel):
    candidate_id: str
    approach: str
    patch: str | None = None  # unified diff
    sandbox_status: Literal["running", "passed", "failed", "not_applicable"]
    test_results: dict | None = None  # {"passed": int, "failed": int, "total": int}
    active: bool = True


class DebugSession(BaseModel):
    session_id: str
    issue_id: str
    mode: Mode
    candidates: list[FixAttempt]


class Recommendation(BaseModel):
    candidate_id: str
    reason: str
