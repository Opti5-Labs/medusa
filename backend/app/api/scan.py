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
import time
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Request, UploadFile
from pydantic import BaseModel, model_validator

from app.config import (
    RATE_SCANS_PER_WINDOW,
    RATE_WINDOW_SECONDS,
    SCAN_TIMEOUT_S,
)
from app.demo.loader import load_demo
from app.errors import MedusaError
from app.github.issues import fetch_github_issues
from app.ingest.github import ingest_github, parse_github_location
from app.ingest.zip_upload import ingest_zip
from app.models.contracts import ScanResult, ScanStatus
from app.pipelines.scan import scan_repo
from app.ratelimit import RateLimiter
from app.store import RunStore, StoreFullError

log = logging.getLogger(__name__)

router = APIRouter()
# Run state lives in memory, so a restart loses every scan made before this.
SERVER_STARTED_AT = time.time()
_MAX_STATUS_IDS = 10

# Shared rate limiter — uses public defaults from config
_scan_limiter = RateLimiter(
    max_calls=RATE_SCANS_PER_WINDOW,
    window_seconds=RATE_WINDOW_SECONDS,
    what="scans",
)

# Seconds kept back from the scan budget for storing and returning the result.
_RESPONSE_MARGIN_S = 5

# GitHub mirrors of the OptiLearn demo repo. Scanning either one runs the
# canned demo instead of a real ingest, so the bundled Whisper sandbox
# scenario and reasoning-mode issues stay available exactly as in source="demo".
_OPTILEARN_DEMO_REPOS = {
    ("ilakiancs", "optilearn"),
    ("chanithaabey", "optilearn-test"),
}


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


def _deadline() -> float:
    return asyncio.get_running_loop().time() + SCAN_TIMEOUT_S - _RESPONSE_MARGIN_S


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("/api/scan", response_model=ScanResult)
async def scan(body: ScanRequest, request: Request) -> ScanResult:
    """
    POST /api/scan

    source="demo"  -> pre-baked OptiLearn result, exempt from rate limit.
    source="github" -> download public repo, scan, return ScanResult, unless
        the URL is a known OptiLearn mirror, in which case it runs the same
        pre-baked demo as source="demo".
    """
    if body.source == "demo":
        result, scenarios = load_demo()
        # Demo records own no files; the store evicts the oldest instead of refusing.
        await _get_store().create(result.scan_id, None, result, scenarios=scenarios)
        log.info("scan: demo scan_id=%s", result.scan_id)
        return result

    # Validate before spending a rate-limit slot on a malformed URL
    repo_url = body.repo_url or ""
    owner, repo, ref, subdir = parse_github_location(repo_url)

    if (owner.lower(), repo.lower()) in _OPTILEARN_DEMO_REPOS:
        result, scenarios = load_demo()
        await _get_store().create(result.scan_id, None, result, scenarios=scenarios)
        log.info(
            "scan: optilearn demo repo url=%s scan_id=%s", repo_url, result.scan_id
        )
        return result

    _scan_limiter.check(request)

    tmp_dir: Path | None = None
    deadline = _deadline()

    try:
        async with asyncio.timeout(SCAN_TIMEOUT_S):
            tmp_dir, extract_root = await ingest_github(repo_url)
            gh_issues, gh_warnings = await fetch_github_issues(owner, repo)
            result = await scan_repo(extract_root, "github", gh_issues, deadline)
            result.warnings.extend(gh_warnings)

            if ref:
                where = f"folder '{subdir}' on " if subdir else ""
                result.warnings.insert(
                    0, f"Scanned {where}branch/tag '{ref}' as requested."
                )

        await _get_store().create(result.scan_id, tmp_dir, result, root=extract_root)
        tmp_dir = None  # store now owns it
        log.info(
            "scan: github owner=%s repo=%s scan_id=%s", owner, repo, result.scan_id
        )
        return result

    except TimeoutError as exc:
        raise MedusaError(
            504,
            f"Scan timed out after {SCAN_TIMEOUT_S} s. "
            "Try a smaller repo, or link to a folder: https://github.com/owner/repo/tree/main/src",
        ) from exc
    except MedusaError:
        raise
    except StoreFullError as exc:
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
    deadline = _deadline()

    try:
        async with asyncio.timeout(SCAN_TIMEOUT_S):
            tmp_dir, extract_root = await ingest_zip(file)
            result = await scan_repo(extract_root, "zip", None, deadline)

        await _get_store().create(result.scan_id, tmp_dir, result, root=extract_root)
        tmp_dir = None  # store now owns it
        log.info("scan: zip filename=%s scan_id=%s", file.filename, result.scan_id)
        return result

    except TimeoutError as exc:
        raise MedusaError(
            504,
            f"Scan timed out after {SCAN_TIMEOUT_S} s. Try a smaller zip.",
        ) from exc
    except MedusaError:
        raise
    except StoreFullError as exc:
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


@router.get("/api/scans/status", response_model=ScanStatus)
async def scans_status(ids: str = "") -> ScanStatus:
    """Which of these scan ids (comma-separated, at most 10) the server still has."""
    wanted = [i for i in ids.split(",") if i][:_MAX_STATUS_IDS]
    store = _get_store()
    alive = [i for i in wanted if await store.get(i) is not None]
    return ScanStatus(alive=alive, server_started_at=SERVER_STARTED_AT)
