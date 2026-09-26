"""
Reproduce pipeline.

sandboxed (OptiLearn demo):
    investigation (recorded Bob run, else Granite) → reproducer on the original
    code in the sandbox → reproduced | not_reproducible
reasoning (general repos):
    one Granite diagnosis over the relevant files → plausible + confidence

Every run ends with a `done` event carrying the final ReproAttempt. Any failure
ends in a visible `error` event and status "error".
"""

import asyncio
import logging
import shutil
import tempfile
import uuid
from pathlib import Path

from app.agents import bob
from app.agents.granite import GraniteError
from app.agents.investigators import diagnose_general, investigate_demo
from app.demo import optilearn
from app.models.contracts import Issue, ReproAttempt
from app.pipelines.context import select_files
from app.sandbox.runner import SandboxResult, docker_available, run_checks
from app.store import ReproRun, RunStore, ScanRecord
from app.streaming import EventChannel

log = logging.getLogger(__name__)


def is_sandboxed(record: ScanRecord, issue: Issue) -> bool:
    return record.scenarios.get(issue.id) == optilearn.SCENARIO_ID


async def start_repro(store: RunStore, record: ScanRecord, issue: Issue) -> ReproRun:
    sandboxed = is_sandboxed(record, issue)
    run = ReproRun(
        attempt=ReproAttempt(
            attempt_id=str(uuid.uuid4()),
            issue_id=issue.id,
            mode="sandboxed" if sandboxed else "reasoning",
            status="running",
        ),
        channel=EventChannel(),
    )
    store.add_repro(run)
    pipeline = (
        _run_sandboxed(run, issue) if sandboxed else _run_reasoning(run, record, issue)
    )
    run.task = asyncio.create_task(_guarded(run, pipeline))
    return run


async def _guarded(run: ReproRun, pipeline) -> None:
    try:
        await pipeline
    except asyncio.CancelledError:
        run.attempt.status = "error"
        await run.channel.emit("medusa", "error", "Run cancelled.")
        raise
    except Exception:
        log.exception("repro: pipeline crashed")
        run.attempt.status = "error"
        await run.channel.emit(
            "medusa", "error", "Unexpected error while reproducing. Please try again."
        )
    finally:
        run.attempt.log = list(run.channel.log)
        await run.channel.done(run.attempt.model_dump_json())


async def run_baseline(
    channel: EventChannel, source: str, captured: list[str] | None = None
) -> SandboxResult:
    """Run the reproducer and checks against the unmodified OptiLearn code."""
    tmp = Path(tempfile.mkdtemp(prefix="medusa_base_"))
    try:
        workdir = optilearn.make_workdir(tmp, "original")

        async def on_line(line: str) -> None:
            if captured is not None:
                captured.append(line)
            await channel.emit(source, "info", line)

        return await run_checks(workdir, on_line)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _runtime_evidence(lines: list[str]) -> str:
    """The failing checks with their assertion output, plus the reproducer's source."""
    failing: list[str] = []
    keep = False
    for line in lines:
        if line.startswith("FAIL"):
            keep = True
        elif not line.startswith(" "):
            keep = False
        if keep:
            failing.append(line)
    reproducer = optilearn.reproducer_source()
    return (
        "Sandbox output for failing checks:\n"
        + ("\n".join(failing) or "(none)")
        + f"\n\nReproducer test that was run:\n{reproducer}"
    )


async def _run_investigators(
    run: ReproRun, issue: Issue, evidence: str | None
) -> str | None:
    """Granite investigation; returns the root cause, or None if unavailable."""
    try:
        synthesis = await investigate_demo(
            issue, optilearn.context_files(), run.channel, evidence
        )
    except GraniteError as exc:
        run.attempt.investigator_source = "unavailable"
        await run.channel.emit("medusa", "warn", f"Investigators unavailable ({exc}).")
        return None
    run.attempt.investigator_source = "granite"
    return synthesis.root_cause


async def _run_sandboxed(run: ReproRun, issue: Issue) -> None:
    ch, attempt = run.channel, run.attempt
    root_cause: str | None = None

    # A recorded Bob session is replayed first, as it was captured.
    replayed = await bob.replay_investigation(ch)
    if replayed:
        attempt.investigator_source = "bob_replay"
        root_cause = replayed.root_cause

    if not await asyncio.to_thread(docker_available):
        attempt.status = "error"
        await ch.emit(
            "sandbox", "error", "The sandbox is not available on this server."
        )
        return

    await ch.emit(
        "sandbox", "info", "Running the reproducer against the original OptiLearn code"
    )
    captured: list[str] = []
    result = await run_baseline(ch, "sandbox", captured)
    if not result.ok:
        attempt.status = "error"
        await ch.emit("sandbox", "error", result.error or "Sandbox run failed.")
        return
    run.baseline = result

    if result.reproducer_passed:
        attempt.status = "not_reproducible"
        attempt.root_cause = "The reproducer passes on the original code, so the reported failure did not occur."
        await ch.emit(
            "sandbox",
            "result",
            "Not reproducible: the reproducer passed on the original code.",
        )
        return

    failures = ", ".join(result.reproducer.failures) if result.reproducer else ""
    await ch.emit(
        "sandbox",
        "result",
        f"Reproduced: the reproducer fails on the original code ({failures}). "
        f"{result.suite.passed}/{result.suite.total} other checks pass.",
    )

    # Without a recorded run, Granite diagnoses the failure the sandbox just observed.
    if not replayed:
        root_cause = await _run_investigators(run, issue, _runtime_evidence(captured))

    attempt.status = "reproduced"
    attempt.root_cause = root_cause or issue.description


async def _run_reasoning(run: ReproRun, record: ScanRecord, issue: Issue) -> None:
    ch, attempt = run.channel, run.attempt
    await ch.emit(
        "medusa",
        "info",
        "Analysis only: this repository's code is read as text and never executed.",
    )
    files = (
        select_files(record.root, record.result.files_scanned, issue)
        if record.root
        else {}
    )
    if not files:
        attempt.status = "error"
        await ch.emit(
            "medusa", "error", "Could not find source files related to this issue."
        )
        return
    try:
        diagnosis = await diagnose_general(issue, files, ch)
    except GraniteError as exc:
        attempt.status = "error"
        await ch.emit("granite", "error", str(exc))
        return
    attempt.status = "plausible"
    attempt.root_cause = diagnosis.root_cause
    attempt.confidence = round(diagnosis.confidence, 2)
