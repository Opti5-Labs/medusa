"""
IBM Bob as an investigator.

BOB_MODE=live (default): runs Bob Shell headless (`bob run --format json`) with
the backend-only BOB_API_KEY. Bob reads a temporary copy of the relevant files,
diagnoses the defect and proposes fixes, and the answer is normalised into an
InvestigatorResult. Guard rails:
    - ask mode, with the edit/execute/mcp/subagent/skill tool groups disabled:
      Bob reads, it never writes files or runs commands
    - the workspace is a throwaway copy of the files shown to it, never a repo
    - the child process gets only PATH, HOME and BOB_API_KEY (no other secrets)
    - --max-cost / --max-turns (BOB_MAX_COST, BOB_MAX_TURNS) and a wall-clock timeout
    - identical requests are answered from a cache to save Bobcoins

BOB_MODE=replay: streams a recorded Bob session from golden/optilearn when one
exists (see golden/optilearn/README.md). BOB_MODE=off: Bob is never used.

Every failure comes back as an InvestigatorResult with the real reason; this
module never raises into the pipelines and never substitutes other results.
"""

import asyncio
import copy
import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app import config
from app.agents.granite import LenientModel, extract_json
from app.agents.results import (
    CandidateFix,
    InvestigatorResult,
    failed,
    unavailable,
)
from app.streaming import EventChannel

log = logging.getLogger(__name__)

GOLDEN_FILE = "investigation.jsonl"
_ALLOWED_SOURCES = {
    "investigator:runtime",
    "investigator:repository",
    "investigator:skeptic",
    "synthesis",
}
_DISABLED_TOOL_GROUPS = "edit,execute,mcp,subagent,skill"
_CACHE_TTL_S = 6 * 3600
_cache: OrderedDict[str, tuple[float, "BobAnswer"]] = OrderedDict()
_loop_semaphores: dict[int, asyncio.Semaphore] = {}


class BobFinding(LenientModel):
    root_cause: str
    evidence: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
    candidate_fixes: list[CandidateFix] = Field(default_factory=list)


# ── Availability ──────────────────────────────────────────────────────────────


def golden_available() -> bool:
    path = config.GOLDEN_DIR / GOLDEN_FILE
    if config.BOB_MODE != "replay" or not path.is_file():
        return False
    try:
        with open(path, encoding="utf-8") as fh:
            header = json.loads(fh.readline())
        return header.get("recorded") is True
    except (OSError, ValueError):
        return False


def live_unavailable_reason() -> str | None:
    """None when Bob can run live now, else a user-facing reason."""
    if config.BOB_MODE == "off":
        return "Bob is turned off on this server (BOB_MODE=off)."
    if config.BOB_MODE == "replay":
        return "live Bob runs are disabled on this server (BOB_MODE=replay)."
    if config.BOB_MODE != "live":
        return f"unknown BOB_MODE {config.BOB_MODE!r} (use live, replay or off)."
    if not config.BOB_API_KEY:
        return "BOB_API_KEY is not set on this server."
    if shutil.which(config.BOB_BINARY) is None:
        return "Bob Shell (`bob`) is not installed on this server."
    return None


# ── Replay (recorded session) ─────────────────────────────────────────────────


async def replay_investigation(channel: EventChannel) -> InvestigatorResult | None:
    """Stream the recorded investigation into *channel*. None if unavailable."""
    if not golden_available():
        return None
    lines = (config.GOLDEN_DIR / GOLDEN_FILE).read_text("utf-8").splitlines()[1:]
    root_cause: str | None = None
    confidence: float | None = None
    for raw in lines:
        if not raw.strip():
            continue
        item = json.loads(raw)
        if "synthesis" in item:
            root_cause = str(item["synthesis"]["root_cause"])
            confidence = item["synthesis"].get("confidence")
            continue
        source = item.get("source")
        if source not in _ALLOWED_SOURCES:
            log.warning("bob replay: skipping event with source %r", source)
            continue
        level = item.get("level", "info")
        await channel.emit(
            source,
            level if level in ("info", "warn", "result") else "info",
            str(item["message"]),
        )
        await asyncio.sleep(config.BOB_REPLAY_DELAY_S)
    if not root_cause:
        return failed("bob", "the recorded Bob session has no synthesis line.")
    return InvestigatorResult(
        investigator="bob",
        status="ok",
        root_cause=root_cause,
        confidence=confidence,
        recorded=True,
    )


# ── Live ──────────────────────────────────────────────────────────────────────


def _semaphore() -> asyncio.Semaphore:
    key = id(asyncio.get_running_loop())
    if key not in _loop_semaphores:
        _loop_semaphores.clear()
        _loop_semaphores[key] = asyncio.Semaphore(config.BOB_MAX_CONCURRENT)
    return _loop_semaphores[key]


def _scrub(text: str) -> str:
    if len(config.BOB_API_KEY) >= 16:
        text = text.replace(config.BOB_API_KEY, "***")
    return text


def _child_env() -> dict[str, str]:
    """Only what Bob Shell needs; no other secrets reach the child process."""
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "/tmp")}
    env["BOB_API_KEY"] = config.BOB_API_KEY
    return env


def _command(workspace: Path, prompt: str) -> list[str]:
    return [
        config.BOB_BINARY,
        "run",
        "--format",
        "json",
        "--mode",
        "ask",
        "--max-cost",
        str(config.BOB_MAX_COST),
        "--max-turns",
        str(config.BOB_MAX_TURNS),
        "--disable-mcp",
        "--disable-subagents",
        "--disable-tool-groups",
        _DISABLED_TOOL_GROUPS,
        "--accept-license",
        "--trust",
        "--log-level",
        "error",
        "--workspace",
        str(workspace),
        prompt,
    ]


def _write_workspace(root: Path, files: dict[str, str]) -> None:
    """Copy the files shown to Bob into *root* (relative paths only)."""
    for key, text in files.items():
        rel = key.partition("#L")[0].lstrip("/")
        if not rel or ".." in Path(rel).parts:
            continue
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


def _is_auth_error(text: str) -> bool:
    lowered = text.lower()
    return any(
        s in lowered
        for s in (
            "invalid or expired api key",
            "api key is required",
            "unauthorized",
            "401",
            "forbidden",
        )
    )


@dataclass
class BobAnswer:
    """Outcome of one Bob Shell run, before it is turned into a specific result."""

    status: Literal["ok", "unavailable", "error", "limit"]
    data: BaseModel | None = None
    error: str | None = None
    cost: float | None = None


def parse_answer(
    stdout: str, stderr: str, returncode: int | None, schema: type[BaseModel]
) -> BobAnswer:
    """Parse Bob Shell's NDJSON output (or failure) against *schema*."""
    errors: list[str] = []
    result: dict | None = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "error":
            errors.append(str(event.get("message", "unknown error")))
        elif event.get("type") == "result":
            result = event

    stats = (result or {}).get("stats") or {}
    cost = stats.get("session_costs")
    cost = round(float(cost), 4) if isinstance(cost, int | float) else None

    if result is None:
        text = (stderr or stdout).strip()
        detail = _scrub(text.splitlines()[-1] if text else "")
        if _is_auth_error(stderr + stdout):
            return BobAnswer(
                "error",
                error=f"Bob authentication failed: {detail or 'API key rejected'}",
            )
        return BobAnswer(
            "error",
            error=f"Bob Shell failed (exit {returncode}): {detail or 'no output'}",
        )

    limit_hits = [
        e
        for e in errors
        if "maximum" in e.lower() and ("turn" in e.lower() or "cost" in e.lower())
    ]
    try:
        data = schema.model_validate(
            extract_json(str(result.get("last_message") or ""))
        )
    except (ValueError, TypeError) as exc:
        if limit_hits:
            return BobAnswer(
                "limit",
                error=(
                    f"Bob stopped at its limit before answering: {limit_hits[0]} "
                    f"(BOB_MAX_TURNS={config.BOB_MAX_TURNS}, BOB_MAX_COST={config.BOB_MAX_COST})."
                ),
                cost=cost,
            )
        reason = (
            errors[0]
            if errors
            else f"its answer was not in the expected format ({type(exc).__name__})"
        )
        return BobAnswer(
            "error",
            error=f"Bob did not return a usable answer: {_scrub(reason)}",
            cost=cost,
        )

    status = str(result.get("status", ""))
    if status and status != "success":
        log.warning("bob: result status %r with a usable answer", status)
    return BobAnswer("ok", data=data, cost=cost)


def _to_investigation(answer: BobAnswer) -> InvestigatorResult:
    if answer.status != "ok" or not isinstance(answer.data, BobFinding):
        if answer.status == "unavailable":
            return unavailable("bob", answer.error or "Bob is unavailable.")
        error = (answer.error or "Bob failed.").replace(
            "usable answer", "usable diagnosis"
        )
        return failed("bob", error, limit=answer.status == "limit", cost=answer.cost)
    finding = answer.data
    return InvestigatorResult(
        investigator="bob",
        status="ok",
        root_cause=finding.root_cause.strip(),
        evidence=[e for e in finding.evidence if e.strip()][:8],
        confidence=finding.confidence,
        candidate_fixes=[
            f for f in finding.candidate_fixes if (f.function_source or f.patch)
        ][:3],
        cost=answer.cost,
    )


def parse_output(
    stdout: str, stderr: str, returncode: int | None
) -> InvestigatorResult:
    """Normalise Bob Shell's output for an investigation into an InvestigatorResult."""
    return _to_investigation(parse_answer(stdout, stderr, returncode, BobFinding))


async def _run_cli(
    prompt: str, files: dict[str, str], schema: type[BaseModel], timeout_s: float
) -> BobAnswer:
    workspace = Path(tempfile.mkdtemp(prefix="medusa_bob_"))
    proc = None
    try:
        _write_workspace(workspace, files)
        proc = await asyncio.create_subprocess_exec(
            *_command(workspace, prompt),
            cwd=workspace,
            env=_child_env(),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout_s)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return BobAnswer(
                "limit",
                error=f"Bob did not finish within {round(timeout_s)} s and was stopped.",
            )
        return parse_answer(
            out.decode("utf-8", "replace"),
            err.decode("utf-8", "replace"),
            proc.returncode,
            schema,
        )
    except FileNotFoundError:
        return BobAnswer(
            "unavailable", error="Bob Shell (`bob`) is not installed on this server."
        )
    except OSError as exc:
        return BobAnswer(
            "error", error=f"Bob Shell could not start: {_scrub(str(exc))}"
        )
    finally:
        if proc is not None and proc.returncode is None:
            proc.kill()
        shutil.rmtree(workspace, ignore_errors=True)


async def ask(
    prompt: str,
    files: dict[str, str],
    schema: type[BaseModel],
    timeout_s: float | None = None,
) -> BobAnswer:
    """One live Bob run answering in *schema*. Never raises; cached when successful."""
    if (reason := live_unavailable_reason()) is not None:
        return BobAnswer("unavailable", error=reason)
    key = hashlib.sha256(
        json.dumps(
            [
                schema.__qualname__,
                prompt,
                sorted(files.items()),
                config.BOB_MAX_COST,
                config.BOB_MAX_TURNS,
            ]
        ).encode()
    ).hexdigest()
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < _CACHE_TTL_S:
        log.info("bob: reusing cached %s answer", schema.__name__)
        return copy.deepcopy(hit[1])
    async with _semaphore():
        answer = await _run_cli(
            prompt, files, schema, timeout_s or config.BOB_TIMEOUT_S
        )
    if answer.status == "ok":
        _cache[key] = (time.monotonic(), answer)
        while len(_cache) > 200:
            _cache.popitem(last=False)
    log.info("bob: %s run %s (cost %s)", schema.__name__, answer.status, answer.cost)
    return copy.deepcopy(answer)


async def investigate(prompt: str, files: dict[str, str]) -> InvestigatorResult:
    """Run one live Bob investigation. Never raises."""
    return _to_investigation(await ask(prompt, files, BobFinding))
