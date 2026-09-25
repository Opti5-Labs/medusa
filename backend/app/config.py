"""
Application configuration — all limits and env vars in one place.
Change limits here, nowhere else.
"""

import os

from dotenv import load_dotenv

# Load backend/.env (or the nearest .env up the tree) so that os.getenv picks
# up local overrides. This is a no-op when the file is absent (deployed env
# vars come from the systemd unit / shell environment instead).
load_dotenv()

# ── Origins ────────────────────────────────────────────────────────────────────
ALLOWED_ORIGIN: str = os.getenv("ALLOWED_ORIGIN", "http://localhost:3000")

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

# ── Rate limits (per IP) ───────────────────────────────────────────────────────
RATE_SCANS_PER_WINDOW: int = 5
RATE_REPRO_DEBUG_PER_WINDOW: int = 3
RATE_WINDOW_SECONDS: int = 600  # 10 minutes

# ── Timeouts ───────────────────────────────────────────────────────────────────
SCAN_TIMEOUT_S: int = int(os.getenv("SCAN_TIMEOUT_S", "90"))
SANDBOX_TIMEOUT_S: int = 90
GRANITE_TIMEOUT_S: int = 30
GRANITE_RETRIES: int = 1

# ── Concurrency ────────────────────────────────────────────────────────────────
MAX_CONCURRENT_SANDBOXES: int = int(os.getenv("MAX_CONCURRENT_SANDBOXES", "6"))
GRANITE_MAX_CONCURRENT_CHUNKS: int = 5

# ── Sandbox image ──────────────────────────────────────────────────────────────
SANDBOX_IMAGE: str = os.getenv("SANDBOX_IMAGE", "medusa-optilearn:latest")

# ── watsonx / Granite ──────────────────────────────────────────────────────────
WATSONX_API_KEY: str = os.getenv("WATSONX_API_KEY", "")
WATSONX_PROJECT_ID: str = os.getenv("WATSONX_PROJECT_ID", "")
WATSONX_URL: str = os.getenv("WATSONX_URL", "https://us-south.ml.cloud.ibm.com")
GRANITE_MODEL_ID: str = os.getenv("GRANITE_MODEL_ID", "")

# ── GitHub ─────────────────────────────────────────────────────────────────────
GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")

# ── Bob ────────────────────────────────────────────────────────────────────────
BOB_MODE: str = os.getenv("BOB_MODE", "replay")  # "replay" | "live"

# ── In-memory store TTL ────────────────────────────────────────────────────────
RUN_TTL_SECONDS: int = 1800  # 30 minutes
