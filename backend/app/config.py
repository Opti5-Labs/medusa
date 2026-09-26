"""
Application configuration — all limits and env vars in one place.
Change limits here, nowhere else.
"""

import os
from pathlib import Path

from dotenv import dotenv_values, find_dotenv, load_dotenv

# Load backend/.env (or the nearest .env up the tree) so that os.getenv picks
# up local overrides. This is a no-op when the file is absent (deployed env
# vars come from the systemd unit / shell environment instead).
load_dotenv()
# load_dotenv never overrides an existing variable, even an empty one. Fill
# variables that are set but empty from .env so an empty export can't hide a key.
for _name, _value in dotenv_values(find_dotenv()).items():
    if _value and not os.environ.get(_name):
        os.environ[_name] = _value


def _env(*names: str, default: str = "") -> str:
    """First non-empty value among *names* (lets IBM_WATSONX_* and WATSONX_* both work)."""
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return default


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes")


# ── Paths ──────────────────────────────────────────────────────────────────────
REPO_ROOT: Path = Path(__file__).resolve().parents[2]
OPTILEARN_DIR: Path = REPO_ROOT / "sandbox" / "optilearn"
OPTILEARN_SRC: Path = OPTILEARN_DIR / "src"
OPTILEARN_PREPARED: Path = OPTILEARN_DIR / "prepared"
GOLDEN_DIR: Path = REPO_ROOT / "golden" / "optilearn"

# ── Origins ────────────────────────────────────────────────────────────────────
ALLOWED_ORIGIN: str = os.getenv("ALLOWED_ORIGIN", "http://localhost:3000")
# Only enable behind a proxy that sets X-Forwarded-For (nginx in deploy/).
TRUST_FORWARDED_FOR: bool = _env_bool("TRUST_FORWARDED_FOR")

# ── Ingest limits ──────────────────────────────────────────────────────────────
# Public defaults are intentionally conservative. Override locally via .env only
# (never commit .env). Suggested local values are documented in .env.example.
MAX_ZIP_SIZE_BYTES: int = int(
    os.getenv("MAX_ZIP_SIZE_BYTES", str(20 * 1024 * 1024))  # default 20 MB
)
MAX_ZIP_FILES: int = int(os.getenv("MAX_ZIP_FILES", "2000"))
MAX_UNCOMPRESSED_BYTES: int = int(
    os.getenv("MAX_UNCOMPRESSED_BYTES", str(200 * 1024 * 1024))  # default 200 MB
)
# GitHub API reports repo size in KB; default cap is 50 MB expressed in KB.
MAX_GITHUB_REPO_SIZE_KB: int = int(
    os.getenv("MAX_GITHUB_REPO_SIZE_KB", str(50 * 1024))  # default 50 MB
)

# ── Scan limits ────────────────────────────────────────────────────────────────
SCAN_MAX_FILES: int = int(os.getenv("SCAN_MAX_FILES", "40"))
SCAN_MAX_LINES: int = int(os.getenv("SCAN_MAX_LINES", "6000"))
SCAN_CHUNK_LINES: int = 400  # files longer than this are split by top-level definitions
SCAN_MAX_ISSUES: int = 25  # cap on scan issues shown, highest priority first

# ── Rate limits (per IP) ───────────────────────────────────────────────────────
RATE_SCANS_PER_WINDOW: int = 5
# "Reproduce and Debug" is two runs, so this allows three full flows per window.
RATE_REPRO_DEBUG_PER_WINDOW: int = 6
RATE_ASK_PER_WINDOW: int = 6
RATE_ASK_INSTANT_PER_WINDOW: int = 60  # instant answers call no model; generous
RATE_ARCH_PER_WINDOW: int = 4
RATE_WINDOW_SECONDS: int = 600  # 10 minutes

# ── Timeouts ───────────────────────────────────────────────────────────────────
SCAN_TIMEOUT_S: int = int(os.getenv("SCAN_TIMEOUT_S", "90"))
SANDBOX_TIMEOUT_S: int = 90
GRANITE_TIMEOUT_S: int = 30
GRANITE_RETRIES: int = 1

# ── Concurrency ────────────────────────────────────────────────────────────────
MAX_CONCURRENT_SANDBOXES: int = int(os.getenv("MAX_CONCURRENT_SANDBOXES", "6"))
GRANITE_MAX_CONCURRENT_CHUNKS: int = 5

# ── Debug candidates ───────────────────────────────────────────────────────────
DEBUG_MIN_CANDIDATES: int = 2
DEBUG_MAX_CANDIDATES: int = 6
DEBUG_DEFAULT_CANDIDATES_DEMO: int = 4
DEBUG_DEFAULT_CANDIDATES_GENERAL: int = 2

# ── General-repo execution (off by default) ─────────────────────────────────────
# Runs a linked repo's own code: installs its dependencies (PyPI-only proxy) and
# runs its tests, under gVisor. Off unless ARBITRARY_EXECUTION=true, and it
# refuses any runtime but runsc unless EXEC_ALLOW_UNSANDBOXED_RUNTIME=true
# (local development only; never on a public server).
ARBITRARY_EXECUTION: bool = _env_bool("ARBITRARY_EXECUTION")
EXEC_IMAGE: str = os.getenv("EXEC_IMAGE", "medusa-pyrunner:latest")
EXEC_RUNTIME: str = os.getenv("EXEC_RUNTIME", "runsc")
EXEC_ALLOW_UNSANDBOXED_RUNTIME: bool = _env_bool("EXEC_ALLOW_UNSANDBOXED_RUNTIME")
EXEC_PROXY_IMAGE: str = os.getenv("EXEC_PROXY_IMAGE", "ubuntu/squid:6.6-24.04_edge")
EXEC_PROXY_NAME: str = "medusa-egress-proxy"
EXEC_NETWORK: str = "medusa-egress"
EXEC_MEM_LIMIT: str = "1g"
EXEC_NANO_CPUS: int = 1_000_000_000  # 1 CPU
EXEC_PIDS_LIMIT: int = 512
EXEC_DEPS_SIZE: str = "1g"  # RAM cap on what one repo can install
EXEC_INSTALL_TIMEOUT_S: int = 300
EXEC_TEST_TIMEOUT_S: int = 180
EXEC_MAX_CONCURRENT: int = 2

# ── Sandbox image ──────────────────────────────────────────────────────────────
SANDBOX_IMAGE: str = os.getenv("SANDBOX_IMAGE", "medusa-optilearn:latest")
SANDBOX_MEM_LIMIT: str = "512m"
SANDBOX_NANO_CPUS: int = 500_000_000  # 0.5 CPU
SANDBOX_PIDS_LIMIT: int = 256
SANDBOX_USER: str = "10001:10001"

# ── watsonx / Granite ──────────────────────────────────────────────────────────
WATSONX_API_KEY: str = _env("IBM_WATSONX_API_KEY", "WATSONX_API_KEY")
WATSONX_PROJECT_ID: str = _env("IBM_WATSONX_PROJECT_ID", "WATSONX_PROJECT_ID")
WATSONX_URL: str = _env(
    "IBM_WATSONX_URL", "WATSONX_URL", default="https://us-south.ml.cloud.ibm.com"
).rstrip("/")
GRANITE_MODEL_ID: str = _env(
    "GRANITE_MODEL_ID", "IBM_WATSONX_MODEL", default="ibm/granite-4-h-small"
)
WATSONX_API_VERSION: str = "2024-10-01"
IBM_IAM_URL: str = "https://iam.cloud.ibm.com/identity/token"

BANNED_MODEL_IDS: frozenset[str] = frozenset(
    {
        "meta-llama/llama-3-405b-instruct",
        "mistralai/mistral-medium-2505",
        "mistralai/mistral-small-3-1-24b-instruct-2503",
    }
)
if GRANITE_MODEL_ID in BANNED_MODEL_IDS or GRANITE_MODEL_ID.split("/")[-1] in {
    m.split("/")[-1] for m in BANNED_MODEL_IDS
}:
    raise RuntimeError(
        f"GRANITE_MODEL_ID {GRANITE_MODEL_ID!r} is banned for this hackathon"
    )

# ── GitHub ─────────────────────────────────────────────────────────────────────
GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")

# ── Bob ────────────────────────────────────────────────────────────────────────
# live   : run Bob Shell (`bob run`) as an investigator, needs BOB_API_KEY (backend only)
# replay : replay a recorded Bob session from golden/optilearn when one exists
# off    : never use Bob
BOB_MODE: str = os.getenv("BOB_MODE", "live").strip().lower()
BOB_API_KEY: str = os.getenv("BOB_API_KEY", "").strip()  # never sent to the frontend
BOB_BINARY: str = os.getenv("BOB_BINARY", "bob")
BOB_MAX_COST: float = float(
    os.getenv("BOB_MAX_COST", "0.25")
)  # Bobcoins per investigation
BOB_MAX_TURNS: int = int(os.getenv("BOB_MAX_TURNS", "6"))
# Server-wide cap on live Bob spend per UTC day (0 disables it). The running
# total survives restarts in BOB_BUDGET_FILE (empty: kept in memory only).
BOB_DAILY_BUDGET: float = float(os.getenv("BOB_DAILY_BUDGET", "5.0"))
BOB_BUDGET_FILE: str = os.getenv("BOB_BUDGET_FILE", "~/.medusa/bob-budget.json")
BOB_TIMEOUT_S: int = int(os.getenv("BOB_TIMEOUT_S", "180"))
BOB_MAX_CONCURRENT: int = 2
BOB_REPLAY_DELAY_S: float = 0.35  # pacing between replayed events

# ── Ask (Q&A) ──────────────────────────────────────────────────────────────────
ASK_ENABLED: bool = _env_bool("ASK_ENABLED", True)
# Off until streaming is verified against the real watsonx API.
ASK_STREAMING: bool = _env_bool("ASK_STREAMING", False)
ASK_MAX_QUESTION_CHARS: int = 1000
ASK_MAX_HISTORY_TURNS: int = 4
ASK_HISTORY_ANSWER_CHARS: int = 600
ASK_MAX_FILES: int = 6
ASK_MAX_CONTEXT_CHARS: int = 16_000
ASK_BOB_MAX_PROMPT_CHARS: int = 20_000
ASK_MAX_REPO_MAP_FILES: int = 150
ASK_MAX_INDEX_FILES: int = 3000
ASK_MAX_SEARCH_BYTES: int = 20_000_000
ASK_MAX_ANSWER_TOKENS: int = 900
ASK_TIMEOUT_S: int = 60
ASK_MAX_READ_BYTES: int = 60_000  # bytes read from one file
ASK_FALLBACK_FILES: int = 3  # README/entry-point files used when nothing matches
ASK_WHOLE_FILE_LINES: int = 300  # files up to this long are sent whole
ASK_WINDOW_LINES: int = 30  # lines of context either side of a keyword hit
ASK_MAX_WINDOWS: int = 4  # keyword-hit windows taken from one long file
ASK_HEAD_LINES: int = 120  # lines taken from the top when a long file has no hit
ASK_MAX_KNOWN_ISSUES: int = 25  # scan issues listed in the prompt
ASK_ISSUE_DESCRIPTION_CHARS: int = 300
ASK_WARNING_CHARS: int = 200
ASK_MAX_WARNINGS: int = 3
ASK_PROMPT_SLACK_CHARS: int = (
    12_000  # room above ASK_MAX_CONTEXT_CHARS for the rest of the prompt
)
ASK_SANDBOX_EVIDENCE_CHARS: int = 4000
ASK_PATCH_CHARS: int = 2500  # per candidate patch
ASK_MAX_CANDIDATES: int = 4  # debug candidates listed in the prompt
ASK_MAX_CITATIONS: int = 8
ASK_STREAM_HOLD_BACK_CHARS: int = (
    200  # streamed tail held back until it is checked for secrets
)
ASK_MAX_RUNS: int = 300  # live ask runs across all users
ASK_FINISHED_KEEP_SECONDS: int = 60  # how long a finished ask run stays readable
ASK_MAX_REMEMBERED_TURNS: int = 20  # question/answer turns stored per scan

# ── In-memory store TTL ────────────────────────────────────────────────────────
RUN_TTL_SECONDS: int = 1800  # 30 minutes

# ── Architecture analysis ──────────────────────────────────────────────────────
# Bounded static analysis of a scan's already-extracted tree. Separate from the
# scan limits above: architecture needs broad structural coverage (directory
# shape, manifests, imports) rather than per-line bug-hunting context, so it
# gets its own, much larger, file/byte/line budget. See app/architecture/.
ARCH_MAX_DISCOVERED_FILES: int = int(os.getenv("ARCH_MAX_DISCOVERED_FILES", "20000"))
ARCH_MAX_PARSED_FILES: int = int(os.getenv("ARCH_MAX_PARSED_FILES", "400"))
ARCH_TIER2_PARSED_FILES: int = 120  # partial-tier parse budget
ARCH_MAX_SOURCE_BYTES: int = int(os.getenv("ARCH_MAX_SOURCE_BYTES", str(8_000_000)))
ARCH_MAX_SOURCE_LINES: int = int(os.getenv("ARCH_MAX_SOURCE_LINES", "200000"))
ARCH_MAX_FILE_BYTES: int = 400_000  # larger files are counted but not parsed
ARCH_MAX_GRAPH_NODES: int = 5_000
ARCH_MAX_GRAPH_EDGES: int = 20_000
ARCH_MAX_COMPONENTS: int = 20
ARCH_MAX_RELATIONSHIPS: int = 60
ARCH_MAX_MERMAID_NODES: int = 24
ARCH_MAX_MERMAID_EDGES: int = 60
ARCH_MAX_MERMAID_CHARS: int = 12_000
ARCH_REPORT_PATH_SAMPLE: int = 300  # cap on files_considered/parsed/skipped samples
ARCH_TIMEOUT_S: int = int(os.getenv("ARCH_TIMEOUT_S", "75"))
ARCH_MAX_CONCURRENT: int = int(os.getenv("ARCH_MAX_CONCURRENT", "2"))
ARCH_MIN_COMPONENT_FILES: int = 2
# Tree-sitter is not wired in yet (stdlib ast + regex only) — reserved for a
# future drop-in extractor behind this flag.
ARCH_USE_TREESITTER: bool = _env_bool("ARCH_USE_TREESITTER")
