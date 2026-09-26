"""
Medusa's in-container harness for running a general Python repository.

Everything untrusted happens in here, inside the gVisor sandbox: installing the
repo's dependencies (which can run setup.py), applying a proposed patch, and
running the repo's tests.

    run.py install      /code (ro) -> /staging (RAM, capped) -> /deps (dependency
                        install through the PyPI-only proxy)
    run.py test         /code (ro) + /deps (ro) + /inputs (ro) -> test results

Inputs for `test` (all optional) in /inputs:
    patch.diff              unified diff applied with `git apply` before tests run
    test_medusa_repro.py    reproducer test added to the repo's tests
    select.txt              pytest node ids / paths to run (default: whole suite)

Output: human-readable progress lines, then exactly one final line:
    MEDUSA_INSTALL {...}   or   MEDUSA_RESULT {...}
"""

import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

CODE = Path("/code")
DEPS = Path("/deps")
STAGING = Path("/staging")  # RAM, capped: bounds what one repo can install
INPUTS = Path("/inputs")
WORK = Path("/work")
REPRO_NAME = "test_medusa_repro.py"

_REQUIREMENT_FILES = [
    "requirements.txt",
    "requirements-dev.txt",
    "requirements-test.txt",
    "requirements_dev.txt",
    "requirements_test.txt",
    "dev-requirements.txt",
    "test-requirements.txt",
    "requirements/test.txt",
    "requirements/tests.txt",
    "requirements/dev.txt",
    "requirements/base.txt",
    "tests/requirements.txt",
]


def _emit(prefix: str, payload: dict) -> None:
    print(prefix + " " + json.dumps(payload), flush=True)


def _pip(args: list[str], timeout: int) -> tuple[bool, str]:
    cmd = [sys.executable, "-m", "pip", "install", "--no-cache-dir",
           "--disable-pip-version-check", "--no-input", "--target", str(STAGING), *args]
    print("$ pip install " + " ".join(args), flush=True)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return False, "timed out"
    tail = (proc.stdout + proc.stderr).strip().splitlines()[-3:]
    for line in tail:
        print("  " + line[:300], flush=True)
    return proc.returncode == 0, "\n".join(tail)[-500:]


def install() -> int:
    """Install the project and its declared test dependencies into /deps."""
    src = WORK / "src"
    shutil.copytree(CODE, src, symlinks=False)
    installed: list[str] = []
    errors: list[str] = []

    for rel in _REQUIREMENT_FILES:
        path = src / rel
        if path.is_file():
            ok, detail = _pip(["-r", str(path)], timeout=600)
            (installed if ok else errors).append(rel if ok else f"{rel}: {detail}")

    if (src / "pyproject.toml").is_file() or (src / "setup.py").is_file():
        # Installing the project itself makes src-layout packages importable.
        ok, detail = _pip([str(src)], timeout=600)
        (installed if ok else errors).append("project" if ok else f"project: {detail}")
        for extra in ("test", "tests", "testing", "dev"):
            ok, _ = _pip([f"{src}[{extra}]"], timeout=600)
            if ok:
                installed.append(f"project[{extra}]")
                break

    # PEP 735 dependency groups (e.g. [dependency-groups] tests = [...]); the
    # heavyweight "dev" group is skipped on purpose.
    pyproject = src / "pyproject.toml"
    if pyproject.is_file():
        try:
            groups = tomllib.loads(pyproject.read_text("utf-8")).get("dependency-groups") or {}
        except (tomllib.TOMLDecodeError, UnicodeDecodeError):
            groups = {}
        for name in ("test", "tests", "testing"):
            if name in groups:
                ok, detail = _pip(["--group", f"{pyproject}:{name}"], timeout=600)
                (installed if ok else errors).append(
                    f"group:{name}" if ok else f"group:{name}: {detail}"
                )

    ok, detail = _pip(["pytest", "pytest-timeout"], timeout=300)
    if not ok:
        errors.append(f"pytest: {detail}")
    # Persist the capped staging area into the dependency volume for the test phase.
    shutil.copytree(STAGING, DEPS, symlinks=True, dirs_exist_ok=True)
    _emit("MEDUSA_INSTALL", {"installed": installed, "errors": errors})
    return 0


class _Collector:
    """Counts outcomes per group: the reproducer test vs the rest of the suite."""

    def __init__(self) -> None:
        self.groups = {
            name: {"passed": 0, "failed": 0, "errors": 0, "total": 0, "failures": []}
            for name in ("reproducer", "suite")
        }
        self.repro_outcome: str | None = None  # passed | failed | error
        self.collection_errors: list[str] = []

    def pytest_collectreport(self, report) -> None:
        if report.failed:
            self.collection_errors.append(report.nodeid or "collection")
            reason = str(report.longrepr).strip().splitlines()[-1:] if report.longrepr else []
            print(f"COLLECT-ERROR {report.nodeid}: {' '.join(reason)[:300]}", flush=True)

    def pytest_runtest_logreport(self, report) -> None:
        if report.when != "call" and not (report.when == "setup" and report.failed):
            return
        group = "reproducer" if REPRO_NAME in report.nodeid else "suite"
        g = self.groups[group]
        g["total"] += 1
        # A setup failure (fixture/import error) is an error, not a test failure.
        outcome = "passed" if report.passed else "error" if report.when == "setup" else "failed"
        if outcome == "passed":
            g["passed"] += 1
        else:
            g["failed" if outcome == "failed" else "errors"] += 1
            if len(g["failures"]) < 50:
                g["failures"].append(report.nodeid)
        if group == "reproducer":
            self.repro_outcome = outcome
        detail = ""
        if outcome != "passed" and report.longrepr is not None:
            crash = getattr(report.longrepr, "reprcrash", None)
            detail = f"\n      {str(crash)[:400]}" if crash else ""
        print(f"{outcome.upper():6} [{group}] {report.nodeid}{detail}", flush=True)


def test() -> int:
    repo = WORK / "repo"
    shutil.copytree(CODE, repo, symlinks=False)
    patch_applied: bool | None = None
    patch_error: str | None = None

    patch = INPUTS / "patch.diff"
    if patch.is_file():
        proc = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", str(patch)],
            cwd=repo, capture_output=True, text=True, timeout=60, check=False,
        )
        patch_applied = proc.returncode == 0
        if not patch_applied:
            patch_error = (proc.stderr or proc.stdout).strip()[-500:]
            print("PATCH  does not apply: " + patch_error, flush=True)
            _emit("MEDUSA_RESULT", {"patch_applied": False, "patch_error": patch_error})
            return 0
        print("PATCH  applied", flush=True)

    # By default the whole suite runs, with the reproducer placed next to the
    # repo's own tests, so one run gives both the reproducer's outcome and any
    # regressions. select.txt narrows the run (the reproducer is always kept).
    targets: list[str] = []
    repro_rel: str | None = None
    repro = INPUTS / REPRO_NAME
    if repro.is_file():
        tests_dir = repo / "tests" if (repo / "tests").is_dir() else repo
        shutil.copy(repro, tests_dir / REPRO_NAME)
        repro_rel = str((tests_dir / REPRO_NAME).relative_to(repo))
    select = INPUTS / "select.txt"
    if select.is_file():
        targets = [t.strip() for t in select.read_text().splitlines() if t.strip()]
        if repro_rel and repro_rel not in targets:
            targets.append(repro_rel)

    # The repo's own (possibly patched) sources must win over the copy of the
    # project that the install phase put in /deps, or a fix would never be seen.
    import_path = [str(repo / "src"), str(repo), str(DEPS)]
    sys.path[:0] = import_path
    os.environ["PYTHONPATH"] = os.pathsep.join(import_path)
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    os.chdir(repo)

    import pytest  # from /deps when the repo pins its own, else the image's

    collector = _Collector()
    args = ["-p", "no:cacheprovider", "-p", "no:terminal", "--continue-on-collection-errors",
            "--timeout=60", "--rootdir", str(repo), *(targets or [])]
    print("Running tests" + (f": {' '.join(targets)}" if targets else " (whole suite)"), flush=True)
    code = pytest.main(args, plugins=[collector])
    if repro_rel and collector.repro_outcome is None:
        # e.g. the repo's pytest config restricts collection to other paths
        collector.repro_outcome = "error"
        collector.collection_errors.append(f"{repro_rel}: reproducer was not collected")
    _emit(
        "MEDUSA_RESULT",
        {
            "patch_applied": patch_applied,
            "exit_code": int(code),
            "groups": collector.groups,
            "repro_outcome": collector.repro_outcome,
            "collection_errors": collector.collection_errors[:20],
        },
    )
    return 0


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "install":
        sys.exit(install())
    if mode == "test":
        sys.exit(test())
    print("usage: run.py install|test", file=sys.stderr)
    sys.exit(2)
