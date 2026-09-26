"""
Sandbox entry point. Runs the Medusa reproducer, the behaviour checks and
OptiLearn's own test suite against the code mounted read-only at /code.

Prints one line per test, then a final line:
    MEDUSA_RESULT {"reproducer": {...}, "suite": {...}}
where each group is {"passed": int, "failed": int, "total": int, "failures": [names]}.
"""

import json
import os
import sys
from pathlib import Path

import pytest

CODE_DIR = Path(os.environ.get("MEDUSA_CODE_DIR", "/code"))
HARNESS_TESTS = Path(__file__).parent / "tests"
REPRODUCER_FILE = "test_reproducer.py"


class _Collector:
    def __init__(self) -> None:
        self.groups: dict[str, dict] = {
            "reproducer": {"passed": 0, "failed": 0, "total": 0, "failures": []},
            "suite": {"passed": 0, "failed": 0, "total": 0, "failures": []},
        }

    def pytest_runtest_logreport(self, report) -> None:
        # A test counts once: its call phase, or setup when setup itself failed.
        if report.when != "call" and not (report.when == "setup" and report.failed):
            return
        group = "reproducer" if REPRODUCER_FILE in report.nodeid else "suite"
        name = report.nodeid.split("/")[-1]
        g = self.groups[group]
        g["total"] += 1
        if report.passed:
            g["passed"] += 1
            print(f"PASS  [{group}] {name}", flush=True)
        else:
            g["failed"] += 1
            g["failures"].append(name)
            print(f"FAIL  [{group}] {name}", flush=True)
            if report.longrepr:
                summary = str(getattr(report.longrepr, "reprcrash", "") or "")
                if summary:
                    print(f"      {summary[:400]}", flush=True)


def main() -> int:
    sys.path.insert(0, str(CODE_DIR))
    os.chdir("/tmp")
    targets = [str(HARNESS_TESTS)]
    code_tests = CODE_DIR / "tests"
    if code_tests.is_dir():
        targets.append(str(code_tests))

    collector = _Collector()
    print("Running reproducer, behaviour checks and OptiLearn test suite", flush=True)
    exit_code = pytest.main(
        ["-p", "no:terminal", "-p", "no:cacheprovider", "--rootdir=/tmp", *targets],
        plugins=[collector],
    )
    print("MEDUSA_RESULT " + json.dumps(collector.groups), flush=True)
    # Exit 0 when pytest ran, whatever the results; non-zero only for harness errors.
    return 0 if exit_code in (0, 1) else int(exit_code)


if __name__ == "__main__":
    sys.exit(main())
