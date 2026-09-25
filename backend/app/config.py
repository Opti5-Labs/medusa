"""
Application configuration — all limits and env vars in one place.
Change limits here, nowhere else.
"""

import os

# ── Origins ────────────────────────────────────────────────────────────────────
ALLOWED_ORIGIN: str = os.getenv("ALLOWED_ORIGIN", "http://localhost:3000")

# ── Ingest limits ──────────────────────────────────────────────────────────────
MAX_ZIP_SIZE_BYTES: int = 20 * 1024 * 1024          # 20 MB
MAX_ZIP_FILES: int = 2_000
MAX_GITHUB_REPO_SIZE_KB: int = 50 * 1024            # 50 MB expressed in KB (GitHub API unit)

# ── Scan limits ────────────────────────────────────────────────────────────────
SCAN_MAX_FILES: int = 40
SCAN_MAX_LINES: int = 6_000

# ── Rate limits (per IP) ───────────────────────────────────────────────────────
RATE_SCANS_PER_WINDOW: int = 5
RATE_REPRO_DEBUG_PER_WINDOW: int = 3
RATE_WINDOW_SECONDS: int = 600                      # 10 minutes

# ── Timeouts ───────────────────────────────────────────────────────────────────
SCAN_TIMEOUT_S: int = 90
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
BOB_MODE: str = os.getenv("BOB_MODE", "replay")     # "replay" | "live"

# ── In-memory store TTL ────────────────────────────────────────────────────────
RUN_TTL_SECONDS: int = 1800                         # 30 minutes
