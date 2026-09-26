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
Grounding = Literal["scan_data", "sandbox_verified", "reasoning"]

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
    # Whether THIS issue's Reproduce/Debug run in a sandbox or as text-only
    # reasoning. Almost always "reasoning" — only issues with a bundled
    # sandbox harness (see demo/optilearn.py) are "sandboxed". A scan's
    # repo_source is not enough to infer this: the demo mixes one sandboxed
    # issue with several reasoning-mode ones.
    mode: Mode = "reasoning"


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
    trigger_conditions: str | None = None  # demo mode only (Granite's runtime finding)
    execution_trace: list[str] = []  # demo mode only (Granite's repository finding)


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
    # General repos run in the sandbox: the model-written test that reproduced
    # the bug (it failed on the original code), shown as evidence.
    reproducer_test: str | None = None


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


class AskCitation(BaseModel):
    file: str
    line: int | None = None


class AskStart(BaseModel):
    ask_id: str


class AskStatus(BaseModel):
    enabled: bool
    granite_available: bool
    bob_available: bool
    bob_reason: str | None = None


class AskAnswer(BaseModel):
    """
    scan_data only when answered_by == "scan"; sandbox_verified only when a
    sandbox actually reproduced the scoped issue on the demo (never for general
    repos); otherwise reasoning.
    """

    ask_id: str
    question: str
    answer: str  # markdown; empty when error is set
    grounding: Grounding
    answered_by: Literal["scan", "granite", "bob"]  # "scan" = no model call
    citations: list[AskCitation] = []
    files_read: list[str] = []  # paths shown to the model
    issue_id: str | None = None
    cost: float | None = None  # Bobcoins, Bob answers only
    notice: str | None = None  # e.g. why Bob answered instead of Granite
    error: str | None = None  # human-readable, set when no answer could be produced


# ── Architecture ───────────────────────────────────────────────────────────────
# Project architecture generation: a deterministic (optionally model-assisted)
# component graph + Mermaid diagram for a scanned repository. See
# app/pipelines/architecture.py. Nothing here implies the code was executed —
# general repos are read as text only, same as the rest of Medusa.

ArchitectureStatus = Literal["running", "complete", "partial", "unavailable", "error"]
ArchitectureSource = Literal["curated", "static", "static_and_model"]
Assisted = Literal["deterministic", "model"]

ComponentType = Literal[
    "frontend",
    "backend",
    "api_layer",
    "service",
    "library",
    "data_store",
    "external_service",
    "worker",
    "cli",
    "config",
    "infrastructure",
    "tests",
    "docs",
    "unknown",
]
RelationshipType = Literal[
    "imports",
    "calls",
    "serves",
    "persists_to",
    "publishes_to",
    "configures",
    "deploys",
    "depends_on",
]


class EvidenceRef(BaseModel):
    """A validated, repository-relative path backing a component or relationship claim."""

    path: str  # posix, relative to the repo root — never absolute
    line: int | None = None  # 1-based
    end_line: int | None = None
    note: str | None = None  # e.g. "FastAPI router include"
    # False = referenced but not present in the analysed tree (e.g. the
    # curated OptiLearn artifact's upstream-only paths). Always True for
    # statically inferred (general-repo) evidence.
    verified: bool = True
    url: str | None = None  # set when verified is False, e.g. an upstream link


class ArchitectureComponent(BaseModel):
    id: str  # stable slug, derived from repository paths
    label: str
    type: ComponentType
    description: str = ""
    paths: list[str] = []  # repo-relative dirs/files this component covers
    evidence: list[EvidenceRef] = []
    confidence: float
    assisted_by: Assisted = "deterministic"
    file_count: int = 0
    rank: float = 0.0  # relative importance; drives ordering and the Mermaid budget


class ArchitectureRelationship(BaseModel):
    source: str  # component id
    target: str  # component id
    type: RelationshipType
    explanation: str = ""
    evidence: list[EvidenceRef] = []
    confidence: float
    assisted_by: Assisted = "deterministic"


class TechnologyStack(BaseModel):
    languages: list[str] = []  # detected, ordered by source-byte share
    frameworks: list[str] = []
    build_systems: list[str] = []
    package_managers: list[str] = []
    test_frameworks: list[str] = []
    unsupported_languages: list[str] = []  # present but NOT parsed by this build


class Entrypoint(BaseModel):
    path: str
    kind: Literal[
        "http_server", "cli", "worker", "web_app", "script", "container", "unknown"
    ]
    detail: str = ""
    evidence: list[EvidenceRef] = []


class DeploymentArtifact(BaseModel):
    kind: Literal[
        "dockerfile", "compose", "ci_workflow", "iac", "systemd", "webserver", "other"
    ]
    path: str
    detail: str = ""
    services: list[str] = []  # compose/CI service or job NAMES only, never values


class ExternalService(BaseModel):
    name: str
    detail: str = ""
    evidence: list[EvidenceRef] = []
    confidence: float
    assisted_by: Assisted = "deterministic"


class DataStore(BaseModel):
    name: str
    kind: Literal[
        "relational",
        "document",
        "key_value",
        "vector",
        "object_store",
        "file",
        "unknown",
    ]
    detail: str = ""
    evidence: list[EvidenceRef] = []
    confidence: float
    assisted_by: Assisted = "deterministic"


class ArchitectureCoverage(BaseModel):
    """Honest, quotable coverage. Rendered as prose in the UI."""

    files_discovered: int  # everything walked, before any filter
    source_files_discovered: int  # recognised source extensions
    source_files_supported: int  # in a language this build can parse
    files_considered: int  # survived the inventory filter
    files_parsed: int
    files_skipped: int
    source_bytes: int
    source_lines: int
    parse_rate: float  # files_parsed / max(1, source_files_supported)
    parse_failures: int
    tier: Literal[1, 2, 3]
    limit_exceeded: str | None = (
        None  # the exact constant name, e.g. "ARCH_MAX_PARSED_FILES"
    )
    skipped_reasons: dict[str, int] = {}  # {"vendored": 812, "binary": 45, ...}
    languages_parsed: dict[str, int] = {}
    languages_not_parsed: dict[str, int] = {}


class ArchitectureReport(BaseModel):
    architecture_id: str
    scan_id: str
    status: ArchitectureStatus
    source: ArchitectureSource
    repo_source: Literal["demo", "github", "zip"]
    summary: str = ""
    technology_stack: TechnologyStack = TechnologyStack()
    entrypoints: list[Entrypoint] = []
    components: list[ArchitectureComponent] = []
    relationships: list[ArchitectureRelationship] = []
    external_services: list[ExternalService] = []
    data_stores: list[DataStore] = []
    deployment: list[DeploymentArtifact] = []
    mermaid: str = ""  # server-generated, sanitized; "" when no graph was produced
    detail_mermaid: str | None = None  # curated verbatim detail diagram (demo only)
    coverage: ArchitectureCoverage | None = None
    warnings: list[str] = []  # things that went wrong but did not stop the run
    limitations: list[str] = []  # things this analysis structurally cannot know
    narrowing_suggestions: list[str] = []  # concrete next steps on partial/unavailable
    files_considered: list[str] = []  # sample, capped — see ARCH_REPORT_PATH_SAMPLE
    files_parsed: list[str] = []  # sample, capped
    files_skipped: list[str] = []  # sample, capped
    curated_version: str | None = None  # curated only, e.g. "optilearn@1.1.0"
    log: list[LogEvent] = []  # snapshotted on completion, like ReproAttempt.log
    generated_at: float
