"""
Reproduce pipeline.

sandboxed (OptiLearn demo):
    reproducer on the original code in the sandbox → reproduced | not_reproducible
    → Bob and Granite investigate the observed failure independently, in parallel
general repos:
    Bob and Granite read the relevant files independently → plausible + confidence
    → when the repo can be run (ARBITRARY_EXECUTION, Python with tests): a
      model-written reproducer test runs in the gVisor sandbox with the repo's
      own suite; if it fails on the original code the bug is reproduced

Every run ends with a `done` event carrying the final ReproAttempt. Any failure
ends in a visible `error` event and status "error".
"""

import asyncio
import logging
import shutil
import tempfile
import uuid
from pathlib import Path

from app import config
from app.agents import panel
from app.agents.reproducer import write_reproducer
from app.demo import optilearn
from app.models.contracts import Issue, ReproAttempt
from app.pipelines.context import select_files_for
from app.pipelines.execution import execution_ineligible_reason
from app.sandbox import pyexec
from app.sandbox.runner import SandboxResult, run_checks, sandbox_unavailable_reason
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


async def _triage(channel: EventChannel, issue: Issue, sandboxed: bool) -> None:
    """Narrate the sandboxed-vs-reasoning decision that already picked this path."""
    where = (
        f" ({issue.file}::{issue.function})" if issue.file and issue.function else ""
    )
    await channel.emit(
        "triage", "info", f"Checking reproduction coverage for '{issue.title}'{where}"
    )
    await channel.emit(
        "triage",
        "result",
        "Bundled sandbox harness found — proceeding with a live reproduction."
        if sandboxed
        else "No bundled sandbox harness for this repository — proceeding with text-only analysis.",
    )


async def _run_sandboxed(run: ReproRun, issue: Issue) -> None:
    ch, attempt = run.channel, run.attempt
    await _triage(ch, issue, sandboxed=True)

    reason = await asyncio.to_thread(sandbox_unavailable_reason)
    if reason is not None:
        attempt.status = "error"
        await ch.emit("sandbox", "error", f"The sandbox is not available: {reason}.")
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
    await _triage(ch, issue, sandboxed=False)
    await ch.emit(
        "medusa",
        "info",
        "Analysing this repository's code as text first.",
    )
    files = select_files_for(
        record.root, record.result.files_scanned, issue, record.scenarios.get(issue.id)
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

    # If this repo can be run, try to confirm the diagnosis with a real failing test.
    why_not = execution_ineligible_reason(record) or await asyncio.to_thread(
        pyexec.unavailable_reason
    )
    if why_not is None:
        await _try_live_reproduction(run, record, issue, files, root_cause)
    elif record.result.repo_source in ("github", "zip"):
        await ch.emit(
            "sandbox", "info", f"Not running this repository's code: {why_not}"
        )


_REPRO_ATTEMPTS = 2


def _repro_feedback(lines: list[str], run: pyexec.TestRun) -> str:
    """What the model is told when its test crashed instead of running."""
    head = "The test did not run cleanly (an error, not a test failure)."
    relevant = [
        line
        for line in lines
        if "test_medusa_repro" in line or line.startswith(("COLLECT-ERROR", "      "))
    ]
    return head + "\n" + "\n".join(relevant[-20:])[:3000]


def _runtime_name() -> str:
    if config.EXEC_RUNTIME == "runsc":
        return "gVisor"
    return f"{config.EXEC_RUNTIME} (development only)"


async def _try_live_reproduction(
    run: ReproRun,
    record: ScanRecord,
    issue: Issue,
    files: dict[str, str],
    root_cause: str,
) -> None:
    """Upgrade a plausible diagnosis to `reproduced` when a real test confirms it."""
    ch, attempt = run.channel, run.attempt
    await ch.emit(
        "sandbox",
        "info",
        f"Running this repository's code in an isolated {_runtime_name()} sandbox",
    )

    async def on_line(line: str) -> None:
        await ch.emit("sandbox", "info", line)

    prep = await pyexec.prepare(record.root, on_line)
    if prep.env is None:
        await ch.emit("sandbox", "warn", f"Could not run the repository: {prep.error}")
        return
    try:
        if prep.env.install_errors:
            await ch.emit(
                "sandbox",
                "warn",
                f"Some dependencies did not install: {'; '.join(prep.env.install_errors)[:500]}",
            )
        previous: str | None = None
        feedback: str | None = None
        for n in range(1, _REPRO_ATTEMPTS + 1):
            await ch.emit(
                "reproducer", "info", f"Writing a reproducer test (attempt {n})"
            )
            draft = await write_reproducer(
                issue, files, root_cause, previous=previous, feedback=feedback
            )
            if draft.source is None:
                await ch.emit(
                    "reproducer", "warn", f"No reproducer test: {draft.error}"
                )
                return
            await ch.emit(
                "reproducer", "info", f"Test written by {draft.author}; running it"
            )
            lines: list[str] = []

            async def collect(line: str, lines: list[str] = lines) -> None:
                lines.append(line)
                await on_line(line)

            result = await pyexec.run_tests(prep.env, collect, repro_test=draft.source)
            if not result.ok:
                await ch.emit(
                    "sandbox", "warn", f"The sandbox run failed: {result.error}"
                )
                return
            if result.repro_outcome == "failed":
                attempt.mode = "sandboxed"
                attempt.status = "reproduced"
                attempt.confidence = None  # confirmed by a test, not estimated
                attempt.reproducer_test = draft.source
                run.reproducer_test = draft.source
                run.exec_baseline = result
                s = result.suite
                await ch.emit(
                    "sandbox",
                    "result",
                    "Reproduced: the reproducer test fails on the original code. "
                    f"The repository's own suite: {s.passed}/{s.total} passed.",
                )
                return
            if result.repro_outcome == "passed":
                # Evidence against the report. Asking for a test that fails
                # anyway would only push the model to assert something wrong.
                await ch.emit(
                    "reproducer",
                    "warn",
                    "The test passed on the original code, so it found no bug: the "
                    "issue may not exist as described.",
                )
                break
            previous, feedback = draft.source, _repro_feedback(lines, result)
            await ch.emit(
                "reproducer",
                "warn",
                "The test errored instead of running; revising it with the output",
            )
        await ch.emit(
            "sandbox",
            "warn",
            "Could not confirm the bug with a failing test, so the result stays an "
            "analysis (plausible), not a reproduction.",
        )
    finally:
        await pyexec.release(prep.env)
