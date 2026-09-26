"""
Execution of general Python repositories (ARBITRARY_EXECUTION, off by default).

This is the one place Medusa runs code from a linked repo or uploaded zip, and
it only does so when the server enables it. Two phases per repository:

    env = await prepare(code_dir, on_line)          # snapshot + install deps (PyPI-only)
    run = await run_tests(env, on_line,             # repo's tests, network off
                          patch=..., repro_test=...)
    await release(env)

Isolation (all phases):
    - gVisor (runtime `runsc`, a user-space kernel); any other runtime is refused
      unless EXEC_ALLOW_UNSANDBOXED_RUNTIME is set for local development
    - repo mounted read-only; all writes go to tmpfs; read-only root filesystem
    - no credentials or host environment; non-root user; all capabilities
      dropped; no-new-privileges; memory, CPU and PID limits; wall-clock timeout
    - dependencies are installed into a RAM area capped at EXEC_DEPS_SIZE, then
      kept in a per-run RAM volume of the same size (held open by an idle
      container) that is deleted afterwards; nothing the repo writes reaches disk
    - container logs are size-capped, and the host reads a bounded amount of
      output; at most EXEC_MAX_ENVS prepared repos exist at once
Install phase: the container's only network is an internal Docker network whose
one exit is a proxy that allows TLS to PyPI and nothing else.
Test phase: no network at all. Patches are applied inside the container.
"""

import asyncio
import json
import logging
import os
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
_SQUID_CONF = config.REPO_ROOT / "sandbox" / "egress-proxy" / "squid.conf"
_loop_semaphores: dict[tuple[int, str], asyncio.Semaphore] = {}


def _semaphore(kind: str = "run") -> asyncio.Semaphore:
    """'run': containers running at once; 'env': prepared repos alive at once."""
    loop = id(asyncio.get_running_loop())
    if (loop, kind) not in _loop_semaphores:
        for key in [k for k in _loop_semaphores if k[0] != loop]:
            del _loop_semaphores[key]
        size = config.EXEC_MAX_CONCURRENT if kind == "run" else config.EXEC_MAX_ENVS
        _loop_semaphores[(loop, kind)] = asyncio.Semaphore(size)
    return _loop_semaphores[(loop, kind)]


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
    code_dir: Path  # this run's own readable snapshot of the repo, mounted at /code
    installed: list[str] = field(default_factory=list)
    install_errors: list[str] = field(default_factory=list)
    holder: str | None = None  # idle container keeping the RAM volume mounted
    holds_slot: bool = False  # counts against EXEC_MAX_ENVS until released


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


def _log_config():
    import docker

    # Docker keeps container output on the host disk; cap it.
    return docker.types.LogConfig(
        type="json-file", config={"max-size": "10m", "max-file": "1"}
    )


def _base_kwargs(code_dir: Path) -> dict:
    """Settings shared by both phases. No environment from the host, ever."""
    return {
        "log_config": _log_config(),
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


def install_kwargs(code_dir: Path, volume: str, proxy_ip: str) -> dict:
    kw = _base_kwargs(code_dir)
    kw["command"] = ["install"]
    kw["network"] = config.EXEC_NETWORK  # internal: the proxy is the only way out
    # By IP: gVisor's network stack bypasses Docker's embedded DNS, so container
    # names do not resolve inside the sandbox. The proxy resolves PyPI itself.
    proxy = f"http://{proxy_ip}:3128"
    kw["environment"] = {
        "HTTPS_PROXY": proxy,
        "HTTP_PROXY": proxy,
        "https_proxy": proxy,
        "http_proxy": proxy,
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


def _ensure_egress(client) -> str:
    """The internal network and the PyPI-only proxy that is its only exit; returns the proxy's IP on it."""
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
        proxy.reload()
    ip = proxy.attrs["NetworkSettings"]["Networks"][config.EXEC_NETWORK]["IPAddress"]
    if not ip:
        raise RuntimeError("the egress proxy has no address on the internal network")
    return ip


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
                if b"\n" not in buffer and len(buffer) > config.EXEC_LOG_MAX_LINE_BYTES:
                    buffer = buffer[: config.EXEC_LOG_MAX_LINE_BYTES] + b"\n"
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
        shown = 0
        try:
            async with asyncio.timeout(timeout_s):
                while (line := await queue.get()) is not None:
                    if line.startswith(prefix):
                        result = line
                    elif line.strip():
                        shown += 1
                        if shown <= config.EXEC_LOG_MAX_LINES:
                            await on_line(line[:2000])
                        elif shown == config.EXEC_LOG_MAX_LINES + 1:
                            await on_line("[further output hidden]")
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


def _snapshot(src: Path) -> Path:
    """
    A private copy of the repo that the sandbox user can read: callers' temp
    dirs are 0700, which the unprivileged container user cannot open. Symlinks
    are copied as links (never followed), and the copy cannot change between
    the install and test phases.
    """
    parent = Path(tempfile.mkdtemp(prefix="medusa_exec_code_"))
    parent.chmod(0o755)
    dest = parent / "code"
    shutil.copytree(src, dest, symlinks=True)
    for root, dirs, files in os.walk(dest):
        for name in dirs:
            path = Path(root) / name
            if not path.is_symlink():
                path.chmod(0o755)
        for name in files:
            path = Path(root) / name
            if not path.is_symlink():
                path.chmod(0o755 if path.stat().st_mode & 0o111 else 0o644)
    dest.chmod(0o755)
    return dest


async def prepare(code_dir: Path, on_line: LineCallback) -> PrepareResult:
    """Snapshot the repo and install its dependencies into a new volume. Never raises."""
    if (reason := await asyncio.to_thread(unavailable_reason)) is not None:
        return PrepareResult(None, reason)
    slots = _semaphore("env")
    if slots.locked():
        await on_line("Waiting for a free sandbox (other repositories are running)")
    await slots.acquire()
    volume = f"medusa-deps-{uuid.uuid4().hex[:12]}"
    try:
        snapshot = await asyncio.to_thread(_snapshot, code_dir)
    except OSError as exc:
        slots.release()
        return PrepareResult(
            None, f"the repository could not be copied for the sandbox: {exc.strerror}"
        )
    env = ExecEnv(volume=volume, code_dir=snapshot, holds_slot=True)
    try:
        client = await asyncio.to_thread(_client)
        proxy_ip = await asyncio.to_thread(_ensure_egress, client)
        await asyncio.to_thread(_create_deps_volume, client, env)
    except Exception as exc:
        log.exception("pyexec: could not prepare the environment")
        await release(env)
        return PrepareResult(
            None, f"the sandbox could not be prepared: {type(exc).__name__}"
        )

    await on_line("Installing the repository's dependencies (PyPI only)")
    async with _semaphore():
        line, error = await _run(
            install_kwargs(env.code_dir, volume, proxy_ip),
            config.EXEC_INSTALL_TIMEOUT_S,
            _INSTALL_PREFIX,
            on_line,
        )
    if error:
        await release(env)
        return PrepareResult(None, f"Installing dependencies failed: {error}")
    try:
        data = json.loads(line[len(_INSTALL_PREFIX) :])
        env.installed = [str(x) for x in data.get("installed", [])][:200]
        env.install_errors = [str(x)[:300] for x in data.get("errors", [])][:20]
    except (ValueError, TypeError, AttributeError):
        await release(env)
        return PrepareResult(None, "the sandbox reported unreadable install results.")
    return PrepareResult(env)


async def run_tests(
    env: ExecEnv,
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
                test_kwargs(env.code_dir, env.volume, inputs),
                config.EXEC_TEST_TIMEOUT_S,
                _RESULT_PREFIX,
                on_line,
            )
        if error:
            return TestRun(ok=False, error=error, duration_s=time.monotonic() - started)
        try:
            run = parse_test_result(line)
        except (ValueError, TypeError, AttributeError):
            return TestRun(
                ok=False,
                error="the sandbox reported unreadable results.",
                duration_s=time.monotonic() - started,
            )
        run.duration_s = time.monotonic() - started
        return run
    finally:
        shutil.rmtree(inputs, ignore_errors=True)


def _create_deps_volume(client, env: ExecEnv) -> None:
    """
    A RAM volume capped at EXEC_DEPS_SIZE: whatever the repo's install writes
    to /deps (setup.py runs as the same user as the copy) never reaches disk.
    A tmpfs volume is unmounted, and emptied, once no container uses it, so an
    idle container holds it open for the environment's lifetime.
    """
    client.volumes.create(
        name=env.volume,
        driver="local",
        driver_opts={
            "type": "tmpfs",
            "device": "tmpfs",
            "o": f"size={config.EXEC_DEPS_SIZE},uid=10001,gid=10001,mode=0755",
        },
    )
    holder = client.containers.run(
        config.EXEC_IMAGE,
        entrypoint=["sleep", "infinity"],
        command=[],
        name=_holder_name(env),
        detach=True,
        runtime=config.EXEC_RUNTIME,
        network_disabled=True,
        read_only=True,
        cap_drop=["ALL"],
        security_opt=["no-new-privileges"],
        user="10001:10001",
        mem_limit="128m",
        pids_limit=128,  # gVisor's own sandbox processes count against this
        environment={},
        log_config=_log_config(),
        volumes={env.volume: {"bind": "/deps", "mode": "ro"}},
    )
    env.holder = holder.id


def _holder_name(env: ExecEnv) -> str:
    return f"medusa-hold-{env.volume}"


def _remove_env(env: ExecEnv) -> None:
    import docker

    client = _client()
    # By name: a holder that failed to start still exists and pins the volume.
    try:
        client.containers.get(_holder_name(env)).remove(force=True)
    except docker.errors.NotFound:
        pass
    try:
        client.volumes.get(env.volume).remove(force=True)
    except docker.errors.NotFound:
        pass


async def release(env: ExecEnv) -> None:
    """Delete the holder, the dependency volume and the code snapshot. Never raises."""
    shutil.rmtree(env.code_dir.parent, ignore_errors=True)
    try:
        await asyncio.to_thread(_remove_env, env)
    except Exception:  # noqa: BLE001  # pragma: no cover
        log.warning("pyexec: could not remove volume %s", env.volume)
    if env.holds_slot:
        env.holds_slot = False
        _semaphore("env").release()
