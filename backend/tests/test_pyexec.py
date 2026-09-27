"""
General-repo execution sandbox (app/sandbox/pyexec.py).

Unit tests run everywhere. The Docker integration test is opt-in
(MEDUSA_EXEC_INTEGRATION=1 plus the medusa-pyrunner image) because it installs
packages from PyPI and runs a real repository's tests.
"""

import asyncio
import json
import os
from pathlib import Path

import pytest

from app import config
from app.sandbox import pyexec


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr("app.config.ARBITRARY_EXECUTION", True)
    monkeypatch.setattr("app.config.EXEC_RUNTIME", "runsc")
    monkeypatch.setattr("app.config.EXEC_ALLOW_UNSANDBOXED_RUNTIME", False)


# ── Refusals ──────────────────────────────────────────────────────────────────


def test_off_by_default():
    assert config.ARBITRARY_EXECUTION is False
    assert "turned off" in pyexec.unavailable_reason()


def test_refuses_any_runtime_but_gvisor(enabled, monkeypatch):
    monkeypatch.setattr("app.config.EXEC_RUNTIME", "runc")
    assert "only gVisor (runsc) is allowed" in pyexec.unavailable_reason()


def test_missing_gvisor_is_reported(enabled, monkeypatch):
    class _Client:
        def ping(self):
            return True

        def info(self):
            return {"Runtimes": {"runc": {}}}

    monkeypatch.setattr(pyexec, "_client", lambda: _Client())
    assert "runsc container runtime is not installed" in pyexec.unavailable_reason()


async def test_prepare_refuses_when_disabled(tmp_path):
    result = await pyexec.prepare(tmp_path, _noop)
    assert result.env is None and "turned off" in result.error


async def _noop(line: str) -> None:
    return None


# ── Container settings ────────────────────────────────────────────────────────


def _common(kw: dict, code: Path) -> None:
    assert kw["runtime"] == "runsc"
    assert kw["read_only"] is True
    assert kw["cap_drop"] == ["ALL"]
    assert kw["security_opt"] == ["no-new-privileges"]
    assert kw["user"] == "10001:10001"
    assert kw["volumes"][str(code.resolve())] == {"bind": "/code", "mode": "ro"}
    for value in kw["environment"].values():
        assert "KEY" not in value and "TOKEN" not in value


def test_install_phase_reaches_only_the_egress_network(enabled, tmp_path, monkeypatch):
    monkeypatch.setenv("WATSONX_API_KEY", "must-not-leak")
    kw = pyexec.install_kwargs(tmp_path, "vol", "172.30.0.2")
    _common(kw, tmp_path)
    assert kw["network"] == config.EXEC_NETWORK
    assert "network_disabled" not in kw
    assert kw["environment"]["HTTPS_PROXY"] == "http://172.30.0.2:3128"
    assert set(kw["environment"]) == {
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "https_proxy",
        "http_proxy",
    }
    assert kw["volumes"]["vol"] == {"bind": "/deps", "mode": "rw"}
    assert f"size={config.EXEC_DEPS_SIZE}" in kw["tmpfs"]["/staging"]


def test_test_phase_has_no_network_and_read_only_inputs(enabled, tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    kw = pyexec.test_kwargs(tmp_path, "vol", inputs)
    _common(kw, tmp_path)
    assert kw["network_disabled"] is True
    assert "network" not in kw
    assert kw["environment"] == {}
    assert kw["volumes"]["vol"] == {"bind": "/deps", "mode": "ro"}
    assert kw["volumes"][str(inputs.resolve())] == {"bind": "/inputs", "mode": "ro"}


# ── Result parsing ────────────────────────────────────────────────────────────


def test_parse_test_result():
    line = "MEDUSA_RESULT " + json.dumps(
        {
            "patch_applied": True,
            "groups": {
                "reproducer": {
                    "passed": 0,
                    "failed": 1,
                    "errors": 0,
                    "total": 1,
                    "failures": ["t::r"],
                },
                "suite": {
                    "passed": 296,
                    "failed": 1,
                    "errors": 0,
                    "total": 297,
                    "failures": ["t::x"],
                },
            },
            "repro_outcome": "failed",
            "collection_errors": [],
        }
    )
    run = pyexec.parse_test_result(line)
    assert run.ok and run.patch_applied is True
    assert run.repro_outcome == "failed"
    assert (run.suite.passed, run.suite.failed, run.suite.total) == (296, 1, 297)


def test_parse_patch_failure():
    line = "MEDUSA_RESULT " + json.dumps(
        {"patch_applied": False, "patch_error": "does not apply"}
    )
    run = pyexec.parse_test_result(line)
    assert run.patch_applied is False and run.patch_error == "does not apply"
    assert run.suite.total == 0


# ── Real containers (opt-in) ──────────────────────────────────────────────────

_FIXTURE_REPO = {
    "pyproject.toml": (
        '[project]\nname = "calc"\nversion = "0.1"\n\n'
        '[build-system]\nrequires = ["setuptools>=61"]\nbuild-backend = "setuptools.build_meta"\n\n'
        '[dependency-groups]\ntests = ["pytest"]\n'
    ),
    # sub() after add() means _FIX has no trailing context away from the end
    # of the file, the shape model-written hunks often have.
    "src/calc/__init__.py": (
        "def add(a, b):\n    return a - b\n\n\ndef sub(a, b):\n    return a - b\n"
    ),
    "tests/test_calc.py": "from calc import add\n\ndef test_zero():\n    assert add(0, 0) == 0\n",
}
_FIX = (
    "--- a/src/calc/__init__.py\n+++ b/src/calc/__init__.py\n"
    "@@ -1,2 +1,2 @@\n def add(a, b):\n-    return a - b\n+    return a + b\n"
)
# Two reproducer tests: one passes even with the bug, one fails. The outcome
# must be "failed" (any failing reproducer test is the evidence).
_REPRO = (
    "from calc import add\n\n"
    "def test_zero_still_works():\n    assert add(0, 0) == 0\n\n"
    "def test_add_adds():\n    assert add(2, 3) == 5\n"
)

integration = pytest.mark.skipif(
    os.environ.get("MEDUSA_EXEC_INTEGRATION") != "1",
    reason="set MEDUSA_EXEC_INTEGRATION=1 (needs Docker, the runner image and PyPI access)",
)


@integration
async def test_reproduce_then_verify_a_fix_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.ARBITRARY_EXECUTION", True)
    runtime = os.environ.get("EXEC_RUNTIME", "runsc")
    monkeypatch.setattr("app.config.EXEC_RUNTIME", runtime)
    monkeypatch.setattr("app.config.EXEC_ALLOW_UNSANDBOXED_RUNTIME", runtime != "runsc")
    for rel, text in _FIXTURE_REPO.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)

    prep = await pyexec.prepare(tmp_path, _noop)
    assert prep.env is not None, prep.error
    # a real install through the PyPI-only proxy (pytest is also in the image,
    # so without this the test would pass with the network broken)
    assert "group:tests" in prep.env.installed, prep.env.install_errors
    try:
        before = await pyexec.run_tests(prep.env, _noop, repro_test=_REPRO)
        assert before.repro_outcome == "failed"  # the bug reproduces
        assert before.suite.passed == 1  # existing test passes on the original

        after = await pyexec.run_tests(prep.env, _noop, patch=_FIX, repro_test=_REPRO)
        assert after.patch_applied is True
        assert after.repro_outcome == "passed"  # the fix works
        assert after.suite.failed == 0  # no regression
    finally:
        await pyexec.release(prep.env)
    await asyncio.sleep(0)


def test_snapshot_is_readable_by_the_sandbox_user_and_keeps_symlinks(tmp_path):
    import shutil

    src = tmp_path / "repo"
    (src / "pkg").mkdir(parents=True)
    (src / "pkg" / "mod.py").write_text("x = 1\n")
    (src / "link").symlink_to("/etc/passwd")
    (src / "pkg" / "mod.py").chmod(0o600)
    (src / "pkg").chmod(0o700)
    src.chmod(0o700)

    snap = pyexec._snapshot(src)
    try:
        assert snap.stat().st_mode & 0o777 == 0o755
        assert (snap / "pkg").stat().st_mode & 0o777 == 0o755
        assert (snap / "pkg" / "mod.py").stat().st_mode & 0o777 == 0o644
        assert (snap / "link").is_symlink()  # copied as a link, never followed
        assert (snap / "link").readlink() == Path("/etc/passwd")
    finally:
        shutil.rmtree(snap.parent)


def _write_repo(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)


def _allow(monkeypatch) -> None:
    monkeypatch.setattr("app.config.ARBITRARY_EXECUTION", True)
    runtime = os.environ.get("EXEC_RUNTIME", "runsc")
    monkeypatch.setattr("app.config.EXEC_RUNTIME", runtime)
    monkeypatch.setattr("app.config.EXEC_ALLOW_UNSANDBOXED_RUNTIME", runtime != "runsc")


@integration
async def test_a_hostile_install_cannot_grow_deps_past_the_cap(tmp_path, monkeypatch):
    _allow(monkeypatch)
    monkeypatch.setattr("app.config.EXEC_DEPS_SIZE", "64m")
    _write_repo(
        tmp_path,
        {
            # setup.py runs as the sandbox user and writes straight to /deps
            "setup.py": (
                "try:\n"
                "    with open('/deps/fill', 'wb') as f:\n"
                "        for _ in range(200):\n"
                "            f.write(bytes(1 << 20))\n"
                "except OSError:\n"
                "    pass\n"
                "from setuptools import setup\n"
                "setup(name='filler', version='0.1')\n"
            ),
            "tests/test_x.py": "def test_x():\n    assert True\n",
        },
    )
    prep = await pyexec.prepare(tmp_path, _noop)
    assert prep.env is not None, prep.error
    try:
        assert any("size limit" in e for e in prep.env.install_errors)
        client = pyexec._client()
        # df, not du: du rounds up and counts metadata, so a full 64 MB reads 65
        out = client.containers.get(prep.env.holder).exec_run(
            ["df", "-m", "--output=used", "/deps"]
        )
        assert int(out.output.split()[-1]) <= 64
    finally:
        await pyexec.release(prep.env)


@integration
async def test_output_floods_are_bounded(tmp_path, monkeypatch):
    _allow(monkeypatch)
    monkeypatch.setattr("app.config.EXEC_LOG_MAX_LINES", 100)
    _write_repo(
        tmp_path,
        {
            # Repo code runs inside the harness process, so it can find the
            # container's real stdout (pytest keeps a copy) and write past
            # capture. Every non-file descriptor: gVisor labels them differently.
            "conftest.py": (
                "import os\n"
                "for fd in map(int, os.listdir('/proc/self/fd')):\n"
                "    try:\n"
                "        if not os.readlink(f'/proc/self/fd/{fd}').startswith('/'):\n"
                "            os.write(fd, b'x' * 500_000)\n"  # one huge line, no newline
                "            for _ in range(3000):\n"
                "                os.write(fd, b'noise\\n')\n"
                "    except OSError:\n"
                "        pass\n"
            ),
            "tests/test_x.py": "def test_x():\n    assert True\n",
        },
    )
    prep = await pyexec.prepare(tmp_path, _noop)
    assert prep.env is not None, prep.error
    lines: list[str] = []

    async def keep(line: str) -> None:
        lines.append(line)

    try:
        run = await pyexec.run_tests(prep.env, keep)
        assert run.ok and run.suite.passed == 1
        assert len(lines) == 101 and lines[-1] == "[further output hidden]"
        assert max(len(line) for line in lines) <= 2000
    finally:
        await pyexec.release(prep.env)


@integration
async def test_real_world_project_layouts(tmp_path, monkeypatch):
    """
    Shapes that broke on real repos: a version taken from git (the archive has
    no .git), a module the build generates, a `tests` extra rather than `test`,
    and a pytest config passing a terminal option (--color).
    """
    _allow(monkeypatch)
    _write_repo(
        tmp_path,
        {
            "pyproject.toml": (
                '[build-system]\nrequires = ["setuptools>=64", "setuptools-scm>=8"]\n'
                'build-backend = "setuptools.build_meta"\n\n'
                '[project]\nname = "vpkg"\ndynamic = ["version"]\n\n'
                '[project.optional-dependencies]\ntests = ["iniconfig"]\n\n'
                '[tool.setuptools_scm]\nversion_file = "src/vpkg/_version.py"\n\n'
                '[tool.pytest.ini_options]\naddopts = "--color=yes"\n'
            ),
            "src/vpkg/__init__.py": "from ._version import version\n\ndef one():\n    return 1\n",
            "tests/test_v.py": (
                "import iniconfig\nfrom vpkg import one, version\n\n"
                "def test_one():\n    assert one() == 1 and version\n"
            ),
        },
    )
    prep = await pyexec.prepare(tmp_path, _noop)
    assert prep.env is not None, prep.error
    try:
        assert "project[tests]" in prep.env.installed, prep.env.install_errors
        run = await pyexec.run_tests(prep.env, _noop)
        assert run.ok, run.error
        assert (run.suite.passed, run.suite.total) == (1, 1), run.collection_errors
    finally:
        await pyexec.release(prep.env)


@integration
async def test_a_pytest_config_error_is_reported_not_zero_tests(tmp_path, monkeypatch):
    _allow(monkeypatch)
    _write_repo(
        tmp_path,
        {
            "pytest.ini": "[pytest]\naddopts = --no-such-option\n",
            "tests/test_x.py": "def test_x():\n    assert True\n",
        },
    )
    prep = await pyexec.prepare(tmp_path, _noop)
    assert prep.env is not None, prep.error
    try:
        run = await pyexec.run_tests(prep.env, _noop)
        assert not run.ok and "pytest could not run the tests" in run.error
    finally:
        await pyexec.release(prep.env)


@integration
async def test_only_real_failures_count_as_reproductions(tmp_path, monkeypatch):
    """A reproducer that crashes by itself is broken, not evidence of the bug."""
    _allow(monkeypatch)
    _write_repo(
        tmp_path,
        {
            "lib.py": "def first(xs):\n    return xs[1]\n",  # the bug: wrong index
            "tests/test_lib.py": "from lib import first\n\ndef test_ok():\n    assert first([1, 2]) == 2\n",
        },
    )
    cases = {
        # reads a file relative to __file__ that does not exist: crashes by itself
        "from pathlib import Path\n\ndef test_x():\n"
        "    assert 'x' in (Path(__file__).parent / 'docs' / 'a.txt').read_text()\n": "error",
        # the project's own code raises: that is the bug showing
        "from lib import first\n\ndef test_x():\n    assert first([7]) == 7\n": "failed",
        # a plain assertion failure
        "from lib import first\n\ndef test_x():\n    assert first([7, 8]) == 7\n": "failed",
    }
    prep = await pyexec.prepare(tmp_path, _noop)
    assert prep.env is not None, prep.error
    try:
        for source, expected in cases.items():
            run = await pyexec.run_tests(prep.env, _noop, repro_test=source)
            assert run.repro_outcome == expected, source
    finally:
        await pyexec.release(prep.env)
