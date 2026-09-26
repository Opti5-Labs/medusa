"""
Sandbox runner tests. The container tests need Docker and the built image
(docker build -t medusa-optilearn:latest sandbox/optilearn) and are skipped otherwise.
"""

import pytest

from app import config
from app.demo import optilearn
from app.sandbox import runner

needs_docker = pytest.mark.skipif(
    not runner.docker_available(), reason="Docker or the sandbox image is not available"
)


def test_unavailable_reason_names_the_actual_cause(monkeypatch):
    """A missing SDK, a stopped daemon and an unbuilt image need different fixes."""

    def boom(*_a, **_kw):
        raise RuntimeError("nope")

    monkeypatch.setattr(runner, "_client", boom)
    reason = runner.sandbox_unavailable_reason()
    assert reason is not None and "daemon is not reachable" in reason
    assert runner.docker_available() is False

    class _NoImage:
        def ping(self):
            return True

        @property
        def images(self):
            raise RuntimeError("ImageNotFound")

    monkeypatch.setattr(runner, "_client", lambda: _NoImage())
    reason = runner.sandbox_unavailable_reason()
    assert reason is not None and config.SANDBOX_IMAGE in reason
    assert "docker build" in reason  # tells the user how to fix it


def test_container_is_locked_down(tmp_path):
    kw = runner._container_kwargs(tmp_path)
    assert kw["network_disabled"] is True
    assert kw["read_only"] is True
    assert kw["cap_drop"] == ["ALL"]
    assert kw["security_opt"] == ["no-new-privileges"]
    assert kw["environment"] == {}
    assert kw["user"] != "root" and not kw["user"].startswith("0")
    assert kw["mem_limit"] == config.SANDBOX_MEM_LIMIT
    assert kw["nano_cpus"] == config.SANDBOX_NANO_CPUS
    assert kw["pids_limit"] == config.SANDBOX_PIDS_LIMIT
    assert list(kw["volumes"].values()) == [{"bind": "/code", "mode": "ro"}]


def test_parse_result_line():
    line = (
        'MEDUSA_RESULT {"reproducer": {"passed": 0, "failed": 1, "total": 1, "failures": ["r"]},'
        ' "suite": {"passed": 6, "failed": 0, "total": 6, "failures": []}}'
    )
    reproducer, suite = runner._parse_result(line)
    assert reproducer.failures == ["r"] and suite.passed == 6


async def _run(tmp_path, target_text=None):
    lines: list[str] = []

    async def on_line(line: str) -> None:
        lines.append(line)

    workdir = optilearn.make_workdir(tmp_path, "code", target_text)
    return await runner.run_checks(workdir, on_line), lines


@needs_docker
async def test_original_code_reproduces_the_bug(tmp_path):
    result, lines = await _run(tmp_path)
    assert result.ok, result.error
    assert not result.reproducer_passed
    assert result.suite.failed == 0 and result.suite.total >= 6
    assert any("FAIL" in line for line in lines)


@needs_docker
@pytest.mark.parametrize(
    ("index", "reproducer_fixed", "regressions"),
    [(0, True, 0), (1, True, 0), (2, True, 1), (3, False, 0)],
    ids=["minimal-guard", "validate-repo-id", "always-default", "normalise-path"],
)
async def test_prepared_candidates_real_outcomes(
    tmp_path, index, reproducer_fixed, regressions
):
    source = optilearn.load_prepared()[index].source
    result, _ = await _run(tmp_path, optilearn.splice(source))
    assert result.ok, result.error
    assert result.reproducer_passed is reproducer_fixed
    assert result.suite.failed == regressions


@needs_docker
async def test_sandbox_has_no_network(tmp_path):
    # A candidate that tries to reach the network must fail, not hang or succeed.
    source = optilearn.load_prepared()[0].source.replace(
        '    """Prefer local',
        "    import socket\n    socket.create_connection(('1.1.1.1', 53), timeout=3)\n"
        '    """Prefer local',
        1,
    )
    result, _ = await _run(tmp_path, optilearn.splice(source))
    assert result.ok
    assert not result.reproducer_passed
