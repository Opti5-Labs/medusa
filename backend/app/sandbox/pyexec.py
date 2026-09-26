"""
Execution of general Python repositories (ARBITRARY_EXECUTION, off by default).

This is the one place Medusa runs code from a linked repo or uploaded zip, and
it only does so when the server enables it. Two phases per repository:

    env = await prepare(code_dir, on_line)          # install deps (PyPI-only)
    run = await run_tests(env, code_dir, on_line,   # repo's tests, network off
                          patch=..., repro_test=...)
    await release(env)

Isolation (all phases):
    - gVisor (runtime `runsc`, a user-space kernel); any other runtime is refused
      unless EXEC_ALLOW_UNSANDBOXED_RUNTIME is set for local development
    - repo mounted read-only; all writes go to tmpfs; read-only root filesystem
    - no credentials or host environment; non-root user; all capabilities
      dropped; no-new-privileges; memory, CPU and PID limits; wall-clock timeout
    - dependencies are installed into a RAM area capped at EXEC_DEPS_SIZE, then
      persisted to a per-run volume that is deleted afterwards
Install phase: the container's only network is an internal Docker network whose
one exit is a proxy that allows TLS to PyPI and nothing else.
Test phase: no network at all. Patches are applied inside the container.
"""

import asyncio
import json
import logging
import shutil
import tempfile
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from app import config

log = logging.getLogger(__name__)

LineCallback = Callable[[str], Awaitable[None]]
_INSTALL_PREFIX = "MEDUSA_INSTALL "
_RESULT_PREFIX = "MEDUSA_RESULT "
_PROXY_URL = f"http://{config.EXEC_PROXY_NAME}:3128"
_SQUID_CONF = config.REPO_ROOT / "sandbox" / "egress-proxy" / "squid.conf"
_loop_semaphores: dict[int, asyncio.Semaphore] = {}


def _semaphore() -> asyncio.Semaphore:
    key = id(asyncio.get_running_loop())
    if key not in _loop_semaphores:
        _loop_semaphores.clear()
        _loop_semaphores[key] = asyncio.Semaphore(config.EXEC_MAX_CONCURRENT)
    return _loop_semaphores[key]


def _client():
    import docker

    return docker.from_env()


# ── Availability ──────────────────────────────────────────────────────────────


def unavailable_reason() -> str | None:
    """None when general-repo execution can run now, else a user-facing reason."""
    if not config.ARBITRARY_EXECUTION:
        return "running code from linked repositories is turned off on this server."
    runtime = config.EXEC_RUNTIME
    if runtime != "runsc" and not config.EXEC_ALLOW_UNSANDBOXED_RUNTIME:
        return (
            f"refusing to run untrusted code with runtime {runtime!r}: only gVisor "
            "(runsc) is allowed."
        )
    try:
        client = _client()
        client.ping()
        runtimes = client.info().get("Runtimes") or {}
    except Exception:  # noqa: BLE001 - any failure means unavailable
        return "the container engine is not reachable on this server."
    if runtime not in runtimes:
        return f"the {runtime} container runtime is not installed on this server."
    try:
        client.images.get(config.EXEC_IMAGE)
    except Exception:  # noqa: BLE001
        return f"the runner image {config.EXEC_IMAGE} is not built on this server."
    return None


# ── Results ───────────────────────────────────────────────────────────────────


@dataclass
class Group:
    passed: int = 0
    failed: int = 0
    errors: int = 0
    total: int = 0
    failures: list[str] = field(default_factory=list)


@dataclass
class ExecEnv:
    volume: str  # the dependency volume, mounted at /deps
    installed: list[str] = field(default_factory=list)
    install_errors: list[str] = field(default_factory=list)


@dataclass
class PrepareResult:
    env: ExecEnv | None
    error: str | None = None


@dataclass
class TestRun:
    ok: bool  # the harness ran and reported results
    patch_applied: bool | None = None
    patch_error: str | None = None
    reproducer: Group = field(default_factory=Group)
    suite: Group = field(default_factory=Group)
    repro_outcome: str | None = None  # passed | failed | error | None (no reproducer)
    collection_errors: list[str] = field(default_factory=list)
    error: str | None = None
    duration_s: float = 0.0


def _group(data: dict | None) -> Group:
    data = data or {}
    return Group(
        passed=int(data.get("passed", 0)),
        failed=int(data.get("failed", 0)),
        errors=int(data.get("errors", 0)),
        total=int(data.get("total", 0)),
        failures=[str(f) for f in data.get("failures", [])][:50],
    )


def parse_test_result(line: str) -> TestRun:
    data = json.loads(line[len(_RESULT_PREFIX) :])
    groups = data.get("groups") or {}
    return TestRun(
        ok=True,
        patch_applied=data.get("patch_applied"),
        patch_error=data.get("patch_error"),
        reproducer=_group(groups.get("reproducer")),
        suite=_group(groups.get("suite")),
        repro_outcome=data.get("repro_outcome"),
        collection_errors=[str(e) for e in data.get("collection_errors", [])][:20],
    )


# ── Container plumbing ────────────────────────────────────────────────────────


def _base_kwargs(code_dir: Path) -> dict:
    """Settings shared by both phases. No environment from the host, ever."""
    return {
        "image": config.EXEC_IMAGE,
        "detach": True,
        "runtime": config.EXEC_RUNTIME,
        "mem_limit": config.EXEC_MEM_LIMIT,
        "memswap_limit": config.EXEC_MEM_LIMIT,
        "nano_cpus": config.EXEC_NANO_CPUS,
        "pids_limit": config.EXEC_PIDS_LIMIT,
        "read_only": True,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges"],
        "user": "10001:10001",
        "environment": {},
        "volumes": {str(code_dir.resolve()): {"bind": "/code", "mode": "ro"}},
    }


def install_kwargs(code_dir: Path, volume: str) -> dict:
    kw = _base_kwargs(code_dir)
    kw["command"] = ["install"]
    kw["network"] = config.EXEC_NETWORK  # internal: the proxy is the only way out
    kw["environment"] = {
        "HTTPS_PROXY": _PROXY_URL,
        "HTTP_PROXY": _PROXY_URL,
        "https_proxy": _PROXY_URL,
        "http_proxy": _PROXY_URL,
    }
    kw["tmpfs"] = {
        "/tmp": "rw,nosuid,size=512m,mode=1777",
        "/work": "rw,nosuid,size=512m,mode=1777",
        # pip installs here first: RAM capped at EXEC_DEPS_SIZE, so one repo can
        # never install more than that; the result is then copied to /deps.
        "/staging": f"rw,nosuid,size={config.EXEC_DEPS_SIZE},mode=1777",
    }
    kw["volumes"][volume] = {"bind": "/deps", "mode": "rw"}
    return kw


def test_kwargs(code_dir: Path, volume: str, inputs_dir: Path) -> dict:
    kw = _base_kwargs(code_dir)
    kw["command"] = ["test"]
    kw["network_disabled"] = True
    kw["tmpfs"] = {
        "/tmp": "rw,nosuid,size=256m,mode=1777",
        "/work": "rw,nosuid,size=512m,mode=1777",
    }
    kw["volumes"][volume] = {"bind": "/deps", "mode": "ro"}
    kw["volumes"][str(inputs_dir.resolve())] = {"bind": "/inputs", "mode": "ro"}
    return kw


def _ensure_egress(client) -> None:
    """The internal network and the PyPI-only proxy that is its only exit."""
    import docker

    try:
        network = client.networks.get(config.EXEC_NETWORK)
    except docker.errors.NotFound:
        network = client.networks.create(
            config.EXEC_NETWORK, driver="bridge", internal=True
        )
    try:
        proxy = client.containers.get(config.EXEC_PROXY_NAME)
        if proxy.status != "running":
            proxy.start()
    except docker.errors.NotFound:
        proxy = client.containers.run(
            config.EXEC_PROXY_IMAGE,
            name=config.EXEC_PROXY_NAME,
            detach=True,
            restart_policy={"Name": "unless-stopped"},
            volumes={str(_SQUID_CONF): {"bind": "/etc/squid/squid.conf", "mode": "ro"}},
            mem_limit="256m",
        )
    proxy.reload()
    if config.EXEC_NETWORK not in (
        proxy.attrs.get("NetworkSettings", {}).get("Networks") or {}
    ):
        network.connect(proxy)


async def _run(
    kwargs: dict, timeout_s: int, prefix: str, on_line: LineCallback
) -> tuple[str | None, str | None]:
    """Run one container to completion. Returns (result line, error). Never raises."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    container = None
    result: str | None = None

    def pump() -> None:
        try:
            buffer = b""
            for chunk in container.logs(stream=True, follow=True):
                buffer += chunk
                while b"\n" in buffer:
                    raw, buffer = buffer.split(b"\n", 1)
                    loop.call_soon_threadsafe(
                        queue.put_nowait, raw.decode("utf-8", "replace")
                    )
            if buffer:
                loop.call_soon_threadsafe(
                    queue.put_nowait, buffer.decode("utf-8", "replace")
                )
        except Exception as exc:  # noqa: BLE001  # pragma: no cover
            loop.call_soon_threadsafe(queue.put_nowait, f"[log stream error: {exc}]")
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    try:
        client = await asyncio.to_thread(_client)
        container = await asyncio.to_thread(client.containers.run, **kwargs)
        pumping = asyncio.create_task(asyncio.to_thread(pump))
        try:
            async with asyncio.timeout(timeout_s):
                while (line := await queue.get()) is not None:
                    if line.startswith(prefix):
                        result = line
                    elif line.strip():
                        await on_line(line[:2000])
                await asyncio.to_thread(container.wait)
        except TimeoutError:
            await asyncio.to_thread(container.kill)
            return None, f"stopped after the {timeout_s} s time limit."
        finally:
            pumping.cancel()
        if result is None:
            return None, "the container exited without reporting results."
        return result, None
    except Exception as exc:
        log.exception("pyexec: container failed")
        return None, f"the sandbox could not run: {type(exc).__name__}"
    finally:
        if container is not None:
            try:
                await asyncio.to_thread(container.remove, force=True)
            except Exception:  # noqa: BLE001  # pragma: no cover
                log.warning("pyexec: could not remove container %s", container.id)


# ── Public API ────────────────────────────────────────────────────────────────


async def prepare(code_dir: Path, on_line: LineCallback) -> PrepareResult:
    """Install the repo's dependencies into a fresh capped volume. Never raises."""
    if (reason := await asyncio.to_thread(unavailable_reason)) is not None:
        return PrepareResult(None, reason)
    volume = f"medusa-deps-{uuid.uuid4().hex[:12]}"
    try:
        client = await asyncio.to_thread(_client)
        await asyncio.to_thread(_ensure_egress, client)
        # An ordinary volume (a tmpfs volume would lose its contents when the
        # install container exits). Its size is bounded by the capped /staging area.
        await asyncio.to_thread(client.volumes.create, name=volume, driver="local")
    except Exception as exc:
        log.exception("pyexec: could not prepare the environment")
        return PrepareResult(
            None, f"the sandbox could not be prepared: {type(exc).__name__}"
        )

    await on_line("Installing the repository's dependencies (PyPI only)")
    async with _semaphore():
        line, error = await _run(
            install_kwargs(code_dir, volume),
            config.EXEC_INSTALL_TIMEOUT_S,
            _INSTALL_PREFIX,
            on_line,
        )
    env = ExecEnv(volume=volume)
    if error:
        await release(env)
        return PrepareResult(None, f"Installing dependencies failed: {error}")
    data = json.loads(line[len(_INSTALL_PREFIX) :])
    env.installed = [str(x) for x in data.get("installed", [])]
    env.install_errors = [str(x)[:300] for x in data.get("errors", [])]
    return PrepareResult(env)


async def run_tests(
    env: ExecEnv,
    code_dir: Path,
    on_line: LineCallback,
    *,
    patch: str | None = None,
    repro_test: str | None = None,
    select: list[str] | None = None,
) -> TestRun:
    """Run the repo's tests (optionally patched, plus a reproducer). Never raises."""
    started = time.monotonic()
    inputs = Path(tempfile.mkdtemp(prefix="medusa_exec_inputs_"))
    try:
        if patch:
            (inputs / "patch.diff").write_text(
                patch if patch.endswith("\n") else patch + "\n"
            )
        if repro_test:
            (inputs / "test_medusa_repro.py").write_text(repro_test)
        if select:
            (inputs / "select.txt").write_text("\n".join(select) + "\n")
        inputs.chmod(0o755)
        for f in inputs.iterdir():
            f.chmod(0o644)
        async with _semaphore():
            line, error = await _run(
                test_kwargs(code_dir, env.volume, inputs),
                config.EXEC_TEST_TIMEOUT_S,
                _RESULT_PREFIX,
                on_line,
            )
        if error:
            return TestRun(ok=False, error=error, duration_s=time.monotonic() - started)
        run = parse_test_result(line)
        run.duration_s = time.monotonic() - started
        return run
    finally:
        shutil.rmtree(inputs, ignore_errors=True)


async def release(env: ExecEnv) -> None:
    """Delete the dependency volume. Never raises."""
    try:
        client = await asyncio.to_thread(_client)
        vol = await asyncio.to_thread(client.volumes.get, env.volume)
        await asyncio.to_thread(vol.remove, force=True)
    except Exception:  # noqa: BLE001  # pragma: no cover
        log.warning("pyexec: could not remove volume %s", env.volume)
