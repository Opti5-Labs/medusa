"""
Demo scan loader.

Loads the pre-baked OptiLearn ScanResult from optilearn_scan.json,
assigns a fresh scan_id and fresh issue UUIDs on every call so that
IDs are always unique across scans.

No Granite, no network, no rate-limit cost.
"""

import json
import uuid
from functools import lru_cache
from pathlib import Path

from app.models.contracts import Issue, ScanResult

_FIXTURE = Path(__file__).parent / "optilearn_scan.json"


@lru_cache(maxsize=1)
def _load_raw() -> dict:
    with open(_FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


def load_demo_result() -> ScanResult:
    """
    Return a fresh ScanResult with a new scan_id and new issue ids each call.
    The fixture data is cached in memory after the first read.
    """
    raw = _load_raw()

    issues = [
        Issue(
            id=str(uuid.uuid4()),  # always fresh — must be unique across scans
            title=item["title"],
            description=item["description"],
            priority=item["priority"],
            source=item["source"],
            category=item.get("category"),
            file=item.get("file"),
            function=item.get("function"),
            github_url=item.get("github_url"),
        )
        for item in raw["issues"]
    ]

    return ScanResult(
        scan_id=str(uuid.uuid4()),
        repo_source="demo",
        language=raw["language"],
        files_scanned=raw["files_scanned"],
        files_total=raw["files_total"],
        issues=issues,
        warnings=raw["warnings"],
    )
