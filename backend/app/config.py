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

# ── In-memory store TTL ────────────────────────────────────────────────────────
RUN_TTL_SECONDS: int = 1800  # 30 minutes
