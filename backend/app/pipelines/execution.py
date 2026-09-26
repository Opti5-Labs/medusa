"""
When a general repository's issue can be reproduced by running its code.

Execution itself lives in app/sandbox/pyexec.py (gVisor, off by default). This
module only decides whether a scanned repo is worth trying: a linked GitHub
repo or uploaded zip that is a Python project with its own tests.
"""

import os
from pathlib import Path

from app.store import ScanRecord

_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "build", "dist"}
_PYTEST_CONFIGS = (
    "pytest.ini",
    "tox.ini",
    "setup.cfg",
    "pyproject.toml",
    "conftest.py",
)
_MAX_WALK = 5000


def _looks_testable(root: Path) -> tuple[bool, str]:
    python_files = 0
    test_files = 0
    walked = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [
            d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")
        ]
        for name in filenames:
            walked += 1
            if name.endswith(".py"):
                python_files += 1
                if name.startswith("test_") or name.endswith("_test.py"):
                    test_files += 1
        if walked > _MAX_WALK:
            break
    if python_files == 0:
        return False, "it is not a Python project"
    has_config = any((root / name).is_file() for name in _PYTEST_CONFIGS)
    if test_files == 0 and not has_config:
        return False, "it has no Python tests to run"
    return True, ""


def execution_ineligible_reason(record: ScanRecord) -> str | None:
    """
    None when this scan's repo could be run in the sandbox, else why not.
    Does not check whether execution is enabled (pyexec.unavailable_reason does).
    """
    if record.result.repo_source not in ("github", "zip"):
        return "only linked repositories and uploaded zips are run"
    if record.root is None or not record.root.is_dir():
        return "the scanned files are no longer available"
    ok, reason = _looks_testable(record.root)
    return None if ok else reason
