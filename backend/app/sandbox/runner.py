"""
The only code-execution entry point in Medusa, and only for the OptiLearn image.

    result = await run_checks(code_dir, on_line)

Runs the harness baked into SANDBOX_IMAGE against *code_dir*, mounted read-only
at /code. *code_dir* is always a Medusa-controlled copy of the bundled OptiLearn
source (never a linked repo or an uploaded zip).

Every container is created with no network, no credentials or environment,
memory/CPU/PID limits, a read-only root filesystem with small tmpfs mounts, all
capabilities dropped and no-new-privileges, as a non-root user. It is killed at
the wall-clock timeout and always removed. A global semaphore caps concurrency.
"""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from app import config

log = logging.getLogger(__name__)

RESULT_PREFIX = "MEDUSA_RESULT "

LineCallback = Callable[[str], Awaitable[None]]

_loop_semaphores: dict[int, asyncio.Semaphore] = {}


def _semaphore() -> asyncio.Semaphore:
    key = id(asyncio.get_running_loop())
    if key not in _loop_semaphores:
        _loop_semaphores.clear()
        _loop_semaphores[key] = asyncio.Semaphore(config.MAX_CONCURRENT_SANDBOXES)
    return _loop_semaphores[key]


@dataclass
class GroupResult:
    passed: int
    failed: int
    total: int
    failures: list[str]


@dataclass
class SandboxResult:
    ok: bool  # the harness ran to completion and reported results
    reproducer: GroupResult | None = None
    suite: GroupResult | None = None
    error: str | None = None
    duration_s: float = 0.0

    @property
    def reproducer_passed(self) -> bool:
        return bool(
            self.reproducer and self.reproducer.total and not self.reproducer.failed
        )


def _client():
    import docker  # imported lazily so the API starts even without the SDK/daemon

    return docker.from_env()


def sandbox_unavailable_reason() -> str | None:
    """
    None when the sandbox can run now, else a specific user-facing reason.

    Each step fails for a different, actionable cause — a missing SDK, a
    stopped daemon and an unbuilt image are not the same problem, and saying
    only "unavailable" sends people looking in the wrong place.
    """
    try:
        import docker  # noqa: F401  # imported here so a missing SDK is its own case
    except ImportError:
        return (
            "the Docker SDK is not installed in this server's environment "
            "(pip install -r requirements.txt)"
        )
    try:
        client = _client()
        client.ping()
    except Exception as exc:  # noqa: BLE001 - any failure means unreachable
        return f"the Docker daemon is not reachable ({type(exc).__name__}); is Docker running?"
    try:
        client.images.get(config.SANDBOX_IMAGE)
    except Exception as exc:  # noqa: BLE001 - not found, or the daemon refused
        return (
            f"the sandbox image {config.SANDBOX_IMAGE} is not available "
            f"({type(exc).__name__}); build it with: "
            f"docker build -t {config.SANDBOX_IMAGE} sandbox/optilearn"
        )
    return None


def docker_available() -> bool:
    return sandbox_unavailable_reason() is None


def _container_kwargs(code_dir: Path) -> dict:
    return {
        "image": config.SANDBOX_IMAGE,
        "detach": True,
        "network_disabled": True,
        "mem_limit": config.SANDBOX_MEM_LIMIT,
        "memswap_limit": config.SANDBOX_MEM_LIMIT,
        "nano_cpus": config.SANDBOX_NANO_CPUS,
        "pids_limit": config.SANDBOX_PIDS_LIMIT,
        "read_only": True,
        "tmpfs": {"/tmp": "rw,nosuid,size=64m", "/work": "rw,nosuid,size=16m"},
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges"],
        "user": config.SANDBOX_USER,
        "environment": {},
        "volumes": {str(code_dir.resolve()): {"bind": "/code", "mode": "ro"}},
    }


def _parse_result(line: str) -> tuple[GroupResult, GroupResult]:
    data = json.loads(line[len(RESULT_PREFIX) :])

    def group(key: str) -> GroupResult:
        g = data[key]
        return GroupResult(
            passed=int(g["passed"]),
            failed=int(g["failed"]),
            total=int(g["total"]),
            failures=[str(f) for f in g.get("failures", [])],
        )

    return group("reproducer"), group("suite")


async def run_checks(code_dir: Path, on_line: LineCallback) -> SandboxResult:
    """Run the harness against *code_dir*. Never raises; failures come back in the result."""
    started = time.monotonic()
    async with _semaphore():
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        container = None
        result_line: str | None = None

        def _pump_logs() -> None:
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
            except Exception as exc:  # noqa: BLE001  # pragma: no cover - daemon dependent
                loop.call_soon_threadsafe(
                    queue.put_nowait, f"[log stream error: {exc}]"
                )
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        try:
            client = await asyncio.to_thread(_client)
            container = await asyncio.to_thread(
                client.containers.run, **_container_kwargs(code_dir)
            )
            pump = asyncio.create_task(asyncio.to_thread(_pump_logs))
            try:
                async with asyncio.timeout(config.SANDBOX_TIMEOUT_S):
                    while (line := await queue.get()) is not None:
                        if line.startswith(RESULT_PREFIX):
                            result_line = line
                        elif line.strip():
                            await on_line(line)
                    exit_info = await asyncio.to_thread(container.wait)
            except TimeoutError:
                await asyncio.to_thread(container.kill)
                return SandboxResult(
                    ok=False,
                    error=f"Sandbox run exceeded the {config.SANDBOX_TIMEOUT_S} s limit and was stopped.",
                    duration_s=time.monotonic() - started,
                )
            finally:
                pump.cancel()

            if result_line is None:
                code = (
                    exit_info.get("StatusCode") if isinstance(exit_info, dict) else None
                )
                return SandboxResult(
                    ok=False,
                    error=f"Sandbox exited (code {code}) without reporting results.",
                    duration_s=time.monotonic() - started,
                )
            reproducer, suite = _parse_result(result_line)
            return SandboxResult(
                ok=True,
                reproducer=reproducer,
                suite=suite,
                duration_s=time.monotonic() - started,
            )
        except Exception as exc:  # reported in the result, never raised
            log.exception("sandbox: run failed")
            return SandboxResult(
                ok=False,
                error=f"Sandbox could not run: {type(exc).__name__}: {exc}"[:500],
                duration_s=time.monotonic() - started,
            )
        finally:
            if container is not None:
                try:
                    await asyncio.to_thread(container.remove, force=True)
                except Exception:  # noqa: BLE001  # pragma: no cover
                    log.warning("sandbox: failed to remove container %s", container.id)
