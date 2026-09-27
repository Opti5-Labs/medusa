"""
Medusa's in-container harness for running a general Python repository.

Everything untrusted happens in here, inside the gVisor sandbox: installing the
repo's dependencies (which can run setup.py), applying a proposed patch, and
running the repo's tests.

    run.py install      /code (ro) -> /staging (RAM, capped) -> /deps (dependency
                        install through the PyPI-only proxy)
    run.py test         /code (ro) + /deps (ro) + /inputs (ro) -> test results

Inputs for `test` (all optional) in /inputs:
    patch.diff              unified diff applied before tests run (`git apply`,
                            then hunks.py for model-written diffs git refuses)
    test_medusa_repro.py    reproducer test added to the repo's tests
    select.txt              pytest node ids / paths to run (default: whole suite)

Output: human-readable progress lines, then exactly one final line:
    MEDUSA_INSTALL {...}   or   MEDUSA_RESULT {...}
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import hunks

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


_OUT = sys.stdout  # the container's stdout; pytest's own report is captured


def _say(line: str) -> None:
    _OUT.write(line + "\n")
    _OUT.flush()


def _emit(prefix: str, payload: dict) -> None:
    _say(prefix + " " + json.dumps(payload))


# Downloaded archives have no .git, so projects that take their version from
# git (setuptools-scm, hatch-vcs) cannot build without being told one.
_PIP_ENV = {**os.environ, "SETUPTOOLS_SCM_PRETEND_VERSION": "0.0.0"}


def _pip_detail(output: str) -> str:
    """The lines that say what went wrong, not pip's closing boilerplate."""
    lines = [line.strip() for line in output.strip().splitlines() if line.strip()]
    useful = [
        line for line in lines
        if ("error" in line.lower() or "exception" in line.lower() or "failed" in line.lower())
        and not line.startswith(("note:", "hint:", "╰─>"))
    ]
    return "\n".join((useful or lines)[-4:])[-500:]


def _pip(args: list[str], timeout: int) -> tuple[bool, str]:
    cmd = [sys.executable, "-m", "pip", "install", "--no-cache-dir",
           "--disable-pip-version-check", "--no-input", "--target", str(STAGING), *args]
    print("$ pip install " + " ".join(args), flush=True)
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False, env=_PIP_ENV
        )
    except subprocess.TimeoutExpired:
        return False, "timed out"
    output = proc.stdout + proc.stderr
    ok = proc.returncode == 0
    if ok and "does not provide the extra" in output:
        ok = False  # pip only warns about a missing extra
        shown = "no such extra"
    else:
        shown = _pip_detail(output) if not ok else "\n".join(output.strip().splitlines()[-2:])
    for line in shown.splitlines():
        print("  " + line[:300], flush=True)
    return ok, shown


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
    # /deps is a size-capped RAM volume; the repo's own install code may have
    # filled it already, which leaves the test phase without some packages.
    try:
        shutil.copytree(STAGING, DEPS, symlinks=True, dirs_exist_ok=True)
    except (OSError, shutil.Error) as exc:
        errors.append(f"dependencies exceed the sandbox's size limit ({type(exc).__name__})")
    _emit("MEDUSA_INSTALL", {"installed": installed, "errors": errors})
    return 0


def _overlay_generated(repo: Path) -> None:
    """
    Copy files a build generates (e.g. a hatch-vcs `_version.py`, compiled
    modules) from the installed copy of the project into the source tree, so
    the source can be imported first. Files already in the source always win.
    """
    for installed in DEPS.iterdir():
        if not (installed / "__init__.py").is_file():
            continue
        for base in (repo / "src", repo):
            source = base / installed.name
            if (source / "__init__.py").is_file():
                break
        else:
            continue
        for path in installed.rglob("*"):
            if path.is_symlink() or not path.is_file() or "__pycache__" in path.parts:
                continue
            target = source / path.relative_to(installed)
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)


_SHOWN_SUITE_FAILURES = 20


class _Collector:
    """Counts outcomes per group: the reproducer test vs the rest of the suite."""

    def __init__(self) -> None:
        self.groups = {
            name: {"passed": 0, "failed": 0, "errors": 0, "total": 0, "failures": []}
            for name in ("reproducer", "suite")
        }
        self.repro_outcome: str | None = None  # passed | failed | error
        self.collection_errors: list[str] = []
        self.hidden_failures = 0

    def pytest_collectreport(self, report) -> None:
        if report.failed:
            self.collection_errors.append(report.nodeid or "collection")
            reason = str(report.longrepr).strip().splitlines()[-1:] if report.longrepr else []
            _say(f"COLLECT-ERROR {report.nodeid}: {' '.join(reason)[:300]}")

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
            # Any failing reproducer test is the evidence; an error only counts
            # when nothing failed, and "passed" needs every reproducer test to pass.
            rank = {"failed": 3, "error": 2, "passed": 1}
            if rank[outcome] > rank.get(self.repro_outcome or "", 0):
                self.repro_outcome = outcome
        # Only what matters is printed: every reproducer result and the first
        # suite failures. Passing suite tests are counted in the summary instead.
        if group == "suite" and outcome == "passed":
            return
        if group == "suite" and len(g["failures"]) > _SHOWN_SUITE_FAILURES:
            self.hidden_failures += 1
            return
        detail = ""
        if outcome != "passed" and report.longrepr is not None:
            crash = getattr(report.longrepr, "reprcrash", None)
            detail = f"\n      {str(crash)[:400]}" if crash else ""
        _say(f"{outcome.upper():6} [{group}] {report.nodeid}{detail}")

    def summary(self) -> str:
        s = self.groups["suite"]
        line = f"SUITE  {s['passed']}/{s['total']} of the repository's tests passed"
        if s["failed"] or s["errors"]:
            line += f" ({s['failed']} failed, {s['errors']} errors)"
        if self.hidden_failures:
            line += f"; {self.hidden_failures} more failing tests not listed"
        return line


def test() -> int:
    repo = WORK / "repo"
    shutil.copytree(CODE, repo, symlinks=False)
    patch_applied: bool | None = None
    patch_error: str | None = None

    patch = INPUTS / "patch.diff"
    if patch.is_file():
        # Strict first; model-written hunks that git refuses (wrong counts,
        # stale line numbers, missing trailing context) get the forgiving
        # applier, which still needs the old lines to match exactly.
        proc = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", str(patch)],
            cwd=repo, capture_output=True, text=True, timeout=60, check=False,
        )
        patch_applied = proc.returncode == 0
        if not patch_applied:
            try:
                hunks.apply(repo, patch.read_text())
                patch_applied = True
                print("PATCH  git apply refused it; applied by matching hunks", flush=True)
            except hunks.PatchError as exc:
                patch_error = str(exc)[:500]
        if not patch_applied:
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
    _overlay_generated(repo)
    import_path = [str(repo / "src"), str(repo), str(DEPS)]
    sys.path[:0] = import_path
    os.environ["PYTHONPATH"] = os.pathsep.join(import_path)
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    os.chdir(repo)

    import pytest  # from /deps when the repo pins its own, else the image's

    collector = _Collector()
    # pytest's terminal plugin stays loaded (repo configs pass options such as
    # --color that need it); its report is captured, and the collector prints
    # the per-test lines Medusa reads.
    args = ["-p", "no:cacheprovider", "-q", "--no-header", "--continue-on-collection-errors",
            "--timeout=60", "--rootdir", str(repo), *(targets or [])]
    print("Running tests" + (f": {' '.join(targets)}" if targets else " (whole suite)"), flush=True)
    report = io.StringIO()
    with contextlib.redirect_stdout(report), contextlib.redirect_stderr(report):
        code = pytest.main(args, plugins=[collector])
    _say(collector.summary())
    error = None
    if int(code) in (2, 3, 4):  # interrupted, internal error, usage error
        tail = [line for line in report.getvalue().strip().splitlines() if line.strip()][-4:]
        error = f"pytest could not run the tests (exit code {int(code)}): " + " ".join(tail)[:400]
        _say("ERROR  " + error)
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
            "error": error,
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
