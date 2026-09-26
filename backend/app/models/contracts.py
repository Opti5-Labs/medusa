from typing import Literal

from pydantic import BaseModel

# ── Shared literals ────────────────────────────────────────────────────────────

Priority = Literal["Low", "Medium", "High"]
Mode = Literal["sandboxed", "reasoning"]
# Which investigators produced the diagnosis shown in a reproduce run.
InvestigatorSource = Literal[
    "bob_replay", "bob", "granite", "bob_and_granite", "unavailable"
]
# Who produced a fix candidate.
CandidateOrigin = Literal["bob", "granite", "prepared"]

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
    line: int | None = None
    github_url: str | None = None
    found_by: Literal["granite", "bob"] | None = None  # scan issues only


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
    # e.g. "investigator:runtime", "synthesis", "sandbox", "candidate:c2", "granite"
    source: str
    level: Literal["info", "warn", "error", "result"]
    message: str


class InvestigatorReport(BaseModel):
    """One investigator's independent result, as shown to the user."""

    investigator: Literal["bob", "granite"]
    status: Literal["ok", "unavailable", "error", "limit"]
    root_cause: str | None = None
    evidence: list[str] = []
    confidence: float | None = None  # self-reported; never used to rank patches
    proposed_fixes: int = 0
    error: str | None = None  # the real reason when not ok
    cost: float | None = None  # Bobcoins (Bob only)
    recorded: bool = False  # replayed from a recorded Bob session


class ReproAttempt(BaseModel):
    attempt_id: str
    issue_id: str
    mode: Mode
    status: Literal["running", "reproduced", "not_reproducible", "plausible", "error"]
    log: list[LogEvent] = []
    root_cause: str | None = None
    confidence: float | None = None  # reasoning mode only
    investigator_source: InvestigatorSource | None = None
    investigators: list[InvestigatorReport] = []


class TestResults(BaseModel):
    __test__ = False  # not a pytest test class

    passed: int
    failed: int
    total: int
    reproducer_fixed: bool
    regressions: list[str] = []  # checks that passed before the patch and fail after


class PatchStats(BaseModel):
    files_changed: int
    lines_added: int
    lines_removed: int


class FixAttempt(BaseModel):
    candidate_id: str
    approach: str
    patch: str | None = None  # unified diff
    sandbox_status: Literal["running", "passed", "failed", "not_applicable"]
    test_results: TestResults | None = None
    patch_stats: PatchStats | None = None
    origin: CandidateOrigin | None = None
    attempts: int = 1  # 2 when revised once after failing tests
    error: str | None = None  # why a candidate failed before or during its run
    active: bool = True


class DebugSession(BaseModel):
    session_id: str
    issue_id: str
    mode: Mode
    candidates: list[FixAttempt]


class Recommendation(BaseModel):
    candidate_id: str
    reason: str
    verified: bool  # True only when chosen from sandbox results


class DebugDone(BaseModel):
    """Payload of the final `done` event on /api/debug/{session_id}/events."""

    session: DebugSession
    recommendation: Recommendation | None = None
