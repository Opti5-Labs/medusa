"""
Demo scan loader.

Loads the pre-baked OptiLearn ScanResult from optilearn_scan.json,
assigns a fresh scan_id and fresh issue UUIDs on every call so that
IDs are always unique across scans.

No Granite, no network, no rate-limit cost.
"""

import json
import uuid
from functools import cache, lru_cache
from pathlib import Path

from app.demo.optilearn import SCENARIO_ID
from app.models.contracts import Issue, ScanResult

_FIXTURE = Path(__file__).parent / "optilearn_scan.json"
# Real excerpts from the real OptiLearn repo (github.com/Ilakiancs/OptiLearn),
# each pinned to the commit right before the fix that later landed for it. Used
# for the demo's reasoning-mode issues (no bundled sandbox harness for these,
# unlike the Whisper scenario) so the investigation reads real code, not a
# fabricated stand-in. See golden/optilearn_demo_issues/README.md.
_ISSUES_DIR = Path(__file__).parent.parent.parent / "golden" / "optilearn_demo_issues"


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
        scenario = item.get("scenario")
        fields = {k: v for k, v in item.items() if k not in ("id", "scenario")}
        mode = "sandboxed" if scenario == SCENARIO_ID else "reasoning"
        issue = Issue(id=str(uuid.uuid4()), mode=mode, **fields)  # always fresh
        issues.append(issue)
        if scenario:
            scenarios[issue.id] = scenario

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


@cache
def load_issue_context(scenario: str) -> dict[str, str]:
    """
    Bundled real source for one of the demo's reasoning-mode issues, keyed by
    repo-relative path (with a #L<line> suffix so it reads as the excerpt it
    is). Empty if *scenario* has no bundled context (e.g. it names a sandbox
    scenario instead — see is_sandboxed()).
    """
    manifest_path = _ISSUES_DIR / scenario / "_manifest.json"
    if not manifest_path.is_file():
        return {}
    manifest = json.loads(manifest_path.read_text("utf-8"))
    files: dict[str, str] = {}
    for repo_path, entry in manifest.items():
        text = (_ISSUES_DIR / scenario / entry["file"]).read_text("utf-8")
        key = (
            repo_path
            if entry["start_line"] <= 1
            else f"{repo_path}#L{entry['start_line']}"
        )
        files[key] = text
    return files
