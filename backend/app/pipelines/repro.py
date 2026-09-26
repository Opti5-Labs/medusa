"""
Reproduce pipeline.

sandboxed (OptiLearn demo):
    reproducer on the original code in the sandbox → reproduced | not_reproducible
    → Bob and Granite investigate the observed failure independently, in parallel
reasoning (general repos):
    Bob and Granite read the relevant files independently → plausible + confidence

Every run ends with a `done` event carrying the final ReproAttempt. Any failure
ends in a visible `error` event and status "error".
"""

import asyncio
import logging
import shutil
import tempfile
import uuid
from pathlib import Path

from app.agents import panel
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


def runtime_evidence(lines: list[str]) -> str:
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


def _record_investigations(run: ReproRun, results) -> str | None:
    """Store every investigator's result on the run; return the combined diagnosis."""
    run.investigations = results
    run.attempt.investigators = [r.report() for r in results]
    run.attempt.investigator_source = panel.source_of(results)
    return panel.combined_root_cause(results)


async def _run_sandboxed(run: ReproRun, issue: Issue) -> None:
    ch, attempt = run.channel, run.attempt

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

    # Bob and Granite diagnose the failure the sandbox just observed, independently.
    run.evidence = runtime_evidence(captured)
    results = await panel.investigate_demo_panel(
        issue, optilearn.context_files(), run.evidence, optilearn.TARGET_FUNCTION, ch
    )
    root_cause = _record_investigations(run, results)
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
    results = await panel.investigate_general_panel(issue, files, ch)
    root_cause = _record_investigations(run, results)
    if root_cause is None:
        attempt.status = "error"
        await ch.emit(
            "medusa",
            "error",
            "No investigator could analyse this issue (see the reasons above).",
        )
        return
    attempt.status = "plausible"
    attempt.root_cause = root_cause
    attempt.confidence = panel.reported_confidence(results) or 0.0
