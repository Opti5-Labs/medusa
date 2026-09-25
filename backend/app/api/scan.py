"""
Scan API router — POST /api/scan and POST /api/scan/upload.

Rate limiting:
    Both endpoints are rate-limited per IP (RATE_SCANS_PER_WINDOW / RATE_WINDOW_SECONDS).
    The demo endpoint is exempt because it performs no computation.

Error handling:
    All MedusaError exceptions bubble up to the global handler in main.py.
    Temp dirs are always cleaned up: ingest functions delete on failure; on
    success the store owns the tmp_dir and deletes it on TTL expiry / delete().
"""

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Request, UploadFile
from pydantic import BaseModel, model_validator

from app.config import (
    RATE_SCANS_PER_WINDOW,
    RATE_WINDOW_SECONDS,
    SCAN_TIMEOUT_S,
)
from app.demo.loader import load_demo_result
from app.errors import MedusaError
from app.ingest.github import ingest_github, parse_github_url
from app.ingest.zip_upload import ingest_zip
from app.models.contracts import ScanResult
from app.pipelines.scan import scan_tree
from app.ratelimit import RateLimiter
from app.store import RunStore

log = logging.getLogger(__name__)

router = APIRouter()

# Shared rate limiter — uses public defaults from config
_scan_limiter = RateLimiter(
    max_calls=RATE_SCANS_PER_WINDOW,
    window_seconds=RATE_WINDOW_SECONDS,
)


# ── Request model ─────────────────────────────────────────────────────────────


class ScanRequest(BaseModel):
    model_config = {"extra": "forbid"}

    source: Literal["demo", "github"]
    repo_url: str | None = None

    @model_validator(mode="after")
    def _check_repo_url(self) -> "ScanRequest":
        if self.source == "github" and not self.repo_url:
            raise ValueError("repo_url is required when source is 'github'")
        return self


# ── Store accessor — injected by main.py ─────────────────────────────────────

_store: RunStore | None = None


def set_store(store: RunStore) -> None:
    global _store
    _store = store


def _get_store() -> RunStore:
    assert _store is not None, "RunStore not initialised"
    return _store


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("/api/scan", response_model=ScanResult)
async def scan(body: ScanRequest, request: Request) -> ScanResult:
    """
    POST /api/scan

    source="demo"  -> pre-baked OptiLearn result, exempt from rate limit.
    source="github" -> download public repo, scan, return ScanResult.
    """
    if body.source == "demo":
        result = load_demo_result()
        await _get_store().create(result.scan_id, None, result)
        log.info("scan: demo scan_id=%s", result.scan_id)
        return result

    # GitHub path is rate limited
    _scan_limiter.check(request)

    repo_url = body.repo_url or ""
    owner, repo, ref = parse_github_url(repo_url)

    tmp_dir: Path | None = None

    try:
        async with asyncio.timeout(SCAN_TIMEOUT_S):
            tmp_dir, extract_root = await ingest_github(repo_url)

            # Optionally fetch GitHub Issues (non-fatal)
            from app.github.issues import fetch_github_issues

            gh_issues, gh_warnings = await fetch_github_issues(owner, repo)

            result = await asyncio.to_thread(
                scan_tree, extract_root, "github", gh_issues
            )
            result.warnings.extend(gh_warnings)

            # Inform user if a specific branch was scanned
            if ref:
                result.warnings.insert(0, f"Scanned branch/tag '{ref}' as requested.")

        await _get_store().create(result.scan_id, tmp_dir, result)
        tmp_dir = None  # store now owns it
        log.info(
            "scan: github owner=%s repo=%s scan_id=%s", owner, repo, result.scan_id
        )
        return result

    except asyncio.TimeoutError as exc:
        raise MedusaError(
            504,
            f"Scan timed out after {SCAN_TIMEOUT_S} s. "
            "Try a smaller repo or link to a specific subdirectory.",
        ) from exc
    except MedusaError:
        raise
    except RuntimeError as exc:
        # store.create raises RuntimeError when the store is full (503)
        raise MedusaError(503, str(exc)) from exc
    except Exception:
        log.exception("scan: unexpected error for %s/%s", owner, repo)
        raise MedusaError(
            502,
            "Unexpected error while scanning. Please try again or use a smaller repo.",
        )
    finally:
        # Clean up if store.create was never called (error path)
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)


@router.post("/api/scan/upload", response_model=ScanResult)
async def scan_upload(file: UploadFile, request: Request) -> ScanResult:
    """
    POST /api/scan/upload

    Accepts a multipart zip file, safe-extracts, scans, returns ScanResult.
    Rate limited.
    """
    _scan_limiter.check(request)

    tmp_dir: Path | None = None

    try:
        async with asyncio.timeout(SCAN_TIMEOUT_S):
            tmp_dir, extract_root = await ingest_zip(file)

            result = await asyncio.to_thread(scan_tree, extract_root, "zip")

        await _get_store().create(result.scan_id, tmp_dir, result)
        tmp_dir = None  # store now owns it
        log.info("scan: zip filename=%s scan_id=%s", file.filename, result.scan_id)
        return result

    except asyncio.TimeoutError as exc:
        raise MedusaError(
            504,
            f"Scan timed out after {SCAN_TIMEOUT_S} s. Try a smaller zip.",
        ) from exc
    except MedusaError:
        raise
    except RuntimeError as exc:
        raise MedusaError(503, str(exc)) from exc
    except Exception:
        log.exception("scan: unexpected error processing zip %s", file.filename)
        raise MedusaError(
            400,
            "Unexpected error while scanning. Please try again or use a smaller repo.",
        )
    finally:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)
