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
    kw = pyexec.install_kwargs(tmp_path, "vol")
    _common(kw, tmp_path)
    assert kw["network"] == config.EXEC_NETWORK
    assert "network_disabled" not in kw
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
    "src/calc/__init__.py": "def add(a, b):\n    return a - b\n",
    "tests/test_calc.py": "from calc import add\n\ndef test_zero():\n    assert add(0, 0) == 0\n",
}
_FIX = (
    "--- a/src/calc/__init__.py\n+++ b/src/calc/__init__.py\n"
    "@@ -1,2 +1,2 @@\n def add(a, b):\n-    return a - b\n+    return a + b\n"
)
_REPRO = "from calc import add\n\ndef test_add_adds():\n    assert add(2, 3) == 5\n"

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
