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


def load_demo() -> tuple[ScanResult, dict[str, str]]:
    """
    Return a fresh ScanResult plus {issue_id: scenario} for issues the
    sandbox can reproduce. The fixture is cached after the first read.
    """
    raw = _load_raw()
    issues: list[Issue] = []
    scenarios: dict[str, str] = {}
    for item in raw["issues"]:
        fields = {k: v for k, v in item.items() if k not in ("id", "scenario")}
        issue = Issue(id=str(uuid.uuid4()), **fields)  # always fresh
        issues.append(issue)
        if item.get("scenario"):
            scenarios[issue.id] = item["scenario"]

    result = ScanResult(
        scan_id=str(uuid.uuid4()),
        repo_source="demo",
        language=raw["language"],
        files_scanned=raw["files_scanned"],
        files_total=raw["files_total"],
        issues=issues,
        warnings=raw["warnings"],
    )
    return result, scenarios


def load_demo_result() -> ScanResult:
    return load_demo()[0]
