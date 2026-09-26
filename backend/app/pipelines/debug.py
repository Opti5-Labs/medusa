"""
Debug pipeline: the Debug Race.

sandboxed (OptiLearn demo):
    bug gate (reuse the reproduce baseline, or run it now) → candidates: Bob's
    own proposals (up to half the slots), Granite for the rest, prepared
    candidates only for slots neither filled → each
    candidate spliced into its own copy and tested in its own sandbox, in
    parallel → deterministic verification → recommendation
reasoning (general repos):
    Bob's proposed diffs, then Granite's, shown but never applied or run

A candidate that fails never stops the others. The run ends with a `done`
event carrying DebugDone (session + recommendation).
"""

import asyncio
import logging
import math
import tempfile
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from app.agents import panel
from app.agents.fixers import STRATEGIES, diff_candidate, function_candidate
from app.agents.granite import GraniteError, is_configured
from app.agents.results import CandidateFix, InvestigatorResult
from app.demo import optilearn
from app.models.contracts import DebugDone, DebugSession, FixAttempt, Issue
from app.pipelines import verify
from app.pipelines.context import select_files
from app.pipelines.repro import is_sandboxed, run_baseline, runtime_evidence
from app.sandbox.runner import SandboxResult, docker_available, run_checks
from app.store import DebugRun, RunStore, ScanRecord
from app.streaming import EventChannel

log = logging.getLogger(__name__)


async def start_debug(
    store: RunStore, record: ScanRecord, issue: Issue, n_candidates: int
) -> DebugRun:
    sandboxed = is_sandboxed(record, issue)
    candidates = [
        FixAttempt(
            candidate_id=f"c{i + 1}",
            approach=STRATEGIES[i % len(STRATEGIES)],
            sandbox_status="running" if sandboxed else "not_applicable",
        )
        for i in range(n_candidates)
    ]
    run = DebugRun(
        session=DebugSession(
            session_id=str(uuid.uuid4()),
            issue_id=issue.id,
            mode="sandboxed" if sandboxed else "reasoning",
            candidates=candidates,
        ),
        channel=EventChannel(),
    )
    store.add_debug(run)
    if sandboxed:
        pipeline = _run_sandboxed(store, run, issue)
    else:
        pipeline = _run_reasoning(store, run, record, issue)
    run.task = asyncio.create_task(_guarded(run, pipeline))
    return run


async def _guarded(run: DebugRun, pipeline) -> None:
    try:
        await pipeline
    except asyncio.CancelledError:
        await run.channel.emit("medusa", "error", "Run cancelled.")
        raise
    except Exception:
        log.exception("debug: pipeline crashed")
        await run.channel.emit(
            "medusa", "error", "Unexpected error while debugging. Please try again."
        )
    finally:
        for c in run.session.candidates:
            if c.sandbox_status == "running":
                c.sandbox_status = "failed"
                c.error = c.error or "Did not finish."
        done = DebugDone(session=run.session, recommendation=run.recommendation)
        await run.channel.done(done.model_dump_json())


def _investigation(
    store: RunStore, issue: Issue, name: str
) -> InvestigatorResult | None:
    """The given investigator's result from the latest reproduce run, if any."""
    repro = store.latest_repro_for(issue.id)
    if repro is None:
        return None
    return next((r for r in repro.investigations if r.investigator == name), None)


def _own_root_cause(store: RunStore, issue: Issue, name: str) -> str:
    """An investigator's own diagnosis, never the other's (keeps them independent)."""
    result = _investigation(store, issue, name)
    if result is not None and result.ok and result.root_cause:
        return result.root_cause
    return issue.description


# ── Sandboxed (OptiLearn) ─────────────────────────────────────────────────────


async def _bug_gate(
    store: RunStore, run: DebugRun, issue: Issue
) -> tuple[SandboxResult, str] | None:
    """(original-code run, runtime evidence) if the bug reproduces, else None (gate closed)."""
    ch = run.channel
    repro = store.latest_repro_for(issue.id)
    if repro and repro.task and not repro.task.done():
        await ch.emit("gate", "info", "Waiting for the reproduce run to finish")
        await asyncio.wait({repro.task})
    if repro and repro.attempt.status == "not_reproducible":
        await ch.emit(
            "gate",
            "error",
            "Bug gate closed: the bug was not reproduced, so no fixes were tested.",
        )
        return None
    if repro and repro.baseline is not None and repro.attempt.status == "reproduced":
        await ch.emit(
            "gate",
            "info",
            "Bug gate open: reusing the reproduction from the reproduce run",
        )
        return repro.baseline, repro.evidence or ""

    await ch.emit("gate", "info", "Running the reproducer against the original code")
    captured: list[str] = []
    baseline = await run_baseline(ch, "gate", captured)
    if not baseline.ok:
        await ch.emit("gate", "error", baseline.error or "Sandbox run failed.")
        return None
    if baseline.reproducer_passed:
        await ch.emit(
            "gate",
            "error",
            "Bug gate closed: the reproducer passed on the original code.",
        )
        return None
    await ch.emit(
        "gate", "result", "Bug gate open: the reproducer fails on the original code"
    )
    return baseline, runtime_evidence(captured)


async def _bob_fixes(
    store: RunStore, run: DebugRun, issue: Issue, evidence: str
) -> list[CandidateFix]:
    """Bob's proposed fixes: reused from the reproduce run, else Bob investigates now."""
    ch = run.channel
    result = _investigation(store, issue, "bob")
    if result is None:
        result = await panel.bob_demo_only(
            issue, optilearn.context_files(), evidence, optilearn.TARGET_FUNCTION, ch
        )
    elif result.ok:
        await ch.emit("bob", "info", "Using the fixes Bob proposed during reproduce")
    else:
        await ch.emit("bob", "warn", f"Bob has no fixes to offer: {result.error}")
    return [f for f in result.candidate_fixes if f.function_source] if result.ok else []


async def _generate_sources(
    run: DebugRun, issue: Issue, granite_root_cause: str, bob_fixes: list[CandidateFix]
) -> list[tuple[str, str, str] | None]:
    """(approach, function_source, origin) per slot, or None when nothing is available."""
    ch = run.channel
    slots = run.session.candidates
    generated: list[tuple[str, str, str] | None] = [None] * len(slots)

    # Bob's own proposals take up to half the slots; Granite fills the rest.
    n_bob = min(len(bob_fixes), math.ceil(len(slots) / 2))
    for i, fix in enumerate(bob_fixes[:n_bob]):
        generated[i] = (fix.approach, fix.function_source or "", "bob")
    granite_slots = list(range(n_bob, len(slots)))

    if granite_slots and is_configured():
        current = optilearn.target_function_source()
        module = optilearn.fixer_context()
        acceptance = optilearn.acceptance_criteria()
        await ch.emit(
            "granite",
            "info",
            f"Generating {len(granite_slots)} candidate fixes, one strategy each",
        )

        async def one(i: int, strategy: str) -> None:
            try:
                cand = await function_candidate(
                    issue,
                    granite_root_cause,
                    optilearn.TARGET_FUNCTION,
                    current,
                    module,
                    strategy,
                    acceptance,
                )
                optilearn.splice(cand.function_source)  # reject unusable output early
                generated[i] = (cand.approach, cand.function_source, "granite")
            except (GraniteError, optilearn.CandidateRejected) as exc:
                await ch.emit(
                    f"candidate:{slots[i].candidate_id}",
                    "warn",
                    f"Granite candidate unusable: {exc}",
                )

        await asyncio.gather(
            *(
                one(i, STRATEGIES[k % len(STRATEGIES)])
                for k, i in enumerate(granite_slots)
            )
        )
    elif granite_slots:
        await ch.emit(
            "granite",
            "warn",
            "Granite unavailable: Granite is not configured on this server.",
        )

    prepared = iter(optilearn.load_prepared())
    used_prepared = 0
    for i, slot in enumerate(generated):
        if slot is None and (p := next(prepared, None)) is not None:
            generated[i] = (p.approach, p.source, "prepared")
            used_prepared += 1
    if used_prepared:
        await ch.emit(
            "medusa",
            "warn",
            f"Using {used_prepared} prepared candidate(s) for slots neither Bob nor Granite filled.",
        )
    return generated


Reviser = Callable[[str, str], Awaitable[str | None]]


def _feedback(lines: list[str], results) -> str:
    """Failing checks with their assertion output, for a revision round."""
    kept: list[str] = []
    keep = False
    for line in lines:
        if line.startswith("FAIL"):
            keep = True
        elif not line.startswith(" "):
            keep = False
        if keep:
            kept.append(line)
    summary = []
    if not results.reproducer_fixed:
        summary.append("The failing test still fails.")
    if results.regressions:
        summary.append(
            "These existing checks now fail: " + ", ".join(results.regressions)
        )
    return " ".join(summary) + "\n" + "\n".join(kept)


async def _race_one(
    run: DebugRun,
    candidate: FixAttempt,
    source: str,
    baseline: SandboxResult,
    tmp: Path,
    revise: Reviser | None = None,
) -> None:
    """Test one candidate; a failing Granite candidate gets one revision round."""
    ch, cid = run.channel, candidate.candidate_id
    tag = f"candidate:{cid}"

    while True:
        try:
            patched_text = optilearn.splice(source)
        except optilearn.CandidateRejected as exc:
            candidate.sandbox_status, candidate.error = (
                "failed",
                f"Patch does not apply: {exc}",
            )
            await ch.emit(tag, "error", candidate.error)
            return

        candidate.patch = optilearn.unified_diff(patched_text)
        candidate.patch_stats = optilearn.patch_stats(candidate.patch)
        workdir = tmp / f"{cid}-{candidate.attempts}"
        workdir = optilearn.make_workdir(tmp, workdir.name, patched_text)
        run.workdirs[cid] = workdir
        s = candidate.patch_stats
        await ch.emit(
            tag,
            "info",
            f"Patch applied (+{s.lines_added} −{s.lines_removed}); starting sandbox",
        )

        lines: list[str] = []

        async def on_line(line: str, lines: list[str] = lines) -> None:
            lines.append(line)
            await ch.emit(tag, "info", line)

        result = await run_checks(workdir, on_line)
        if not result.ok:
            candidate.sandbox_status, candidate.error = "failed", result.error
            await ch.emit(tag, "error", result.error or "Sandbox run failed.")
            return

        candidate.test_results = r = verify.evaluate(baseline, result)
        if verify.is_passing(r):
            candidate.sandbox_status, candidate.error = "passed", None
            await ch.emit(
                tag,
                "result",
                f"PASSED: reproducer fixed, {r.passed}/{r.total} checks pass, no regressions",
            )
            return

        why = []
        if not r.reproducer_fixed:
            why.append("reproducer still fails")
        if r.regressions:
            why.append(
                f"{len(r.regressions)} regression(s): {', '.join(r.regressions)}"
            )
        candidate.error = "; ".join(why)

        if revise is None or candidate.attempts > 1:
            candidate.sandbox_status = "failed"
            await ch.emit(tag, "result", f"FAILED: {candidate.error}")
            return

        await ch.emit(
            tag,
            "warn",
            f"Attempt 1 failed ({candidate.error}); revising with the test output",
        )
        revised = await revise(source, _feedback(lines, r))
        if revised is None:
            candidate.sandbox_status = "failed"
            await ch.emit(
                tag, "result", f"FAILED: {candidate.error} (revision unavailable)"
            )
            return
        source = revised
        candidate.attempts += 1


def _granite_reviser(
    run: DebugRun, issue: Issue, root_cause: str, candidate: FixAttempt
) -> Reviser:
    """Ask Granite to revise *candidate* once, given the sandbox's test output."""
    strategy = candidate.approach

    async def revise(previous: str, feedback: str) -> str | None:
        tag = f"candidate:{candidate.candidate_id}"
        try:
            cand = await function_candidate(
                issue,
                root_cause,
                optilearn.TARGET_FUNCTION,
                optilearn.target_function_source(),
                optilearn.fixer_context(),
                strategy,
                optilearn.acceptance_criteria(),
                previous=previous,
                feedback=feedback,
            )
            optilearn.splice(cand.function_source)
        except (GraniteError, optilearn.CandidateRejected) as exc:
            await run.channel.emit(tag, "warn", f"Revision unusable: {exc}")
            return None
        return cand.function_source

    return revise


async def _run_sandboxed(store: RunStore, run: DebugRun, issue: Issue) -> None:
    ch = run.channel
    if not await asyncio.to_thread(docker_available):
        await ch.emit(
            "sandbox", "error", "The sandbox is not available on this server."
        )
        for c in run.session.candidates:
            c.sandbox_status, c.error = "failed", "Sandbox unavailable."
        return

    gate = await _bug_gate(store, run, issue)
    if gate is None:
        for c in run.session.candidates:
            c.sandbox_status, c.error = "failed", "Not run: bug gate closed."
        return
    baseline, evidence = gate

    root_cause = _own_root_cause(store, issue, "granite")
    bob_fixes = await _bob_fixes(store, run, issue, evidence)
    sources = await _generate_sources(run, issue, root_cause, bob_fixes)
    run.tmp_dir = Path(tempfile.mkdtemp(prefix="medusa_debug_"))
    jobs = []
    for candidate, slot in zip(run.session.candidates, sources, strict=True):
        if slot is None:
            candidate.sandbox_status, candidate.error = (
                "failed",
                "No candidate could be generated.",
            )
            await ch.emit(
                f"candidate:{candidate.candidate_id}", "error", candidate.error
            )
            continue
        candidate.approach, source, candidate.origin = slot
        reviser = (
            _granite_reviser(run, issue, root_cause, candidate)
            if candidate.origin == "granite"
            else None
        )
        jobs.append(_race_one(run, candidate, source, baseline, run.tmp_dir, reviser))

    await ch.emit(
        "medusa",
        "info",
        f"Debug race: testing {len(jobs)} candidate(s) in parallel sandboxes",
    )
    await asyncio.gather(*jobs)

    run.recommendation = verify.recommend_verified(run.session.candidates)
    if run.recommendation:
        await ch.emit("recommendation", "result", run.recommendation.reason)
    else:
        await ch.emit(
            "recommendation",
            "warn",
            "No candidate passed verification, so none is recommended.",
        )


# ── Reasoning (general repos) ─────────────────────────────────────────────────


async def _run_reasoning(
    store: RunStore, run: DebugRun, record: ScanRecord, issue: Issue
) -> None:
    ch = run.channel
    await ch.emit(
        "medusa", "info", "Analysis only: patches are proposed, never applied or run."
    )
    files = (
        select_files(record.root, record.result.files_scanned, issue)
        if record.root
        else {}
    )
    if not files:
        await ch.emit(
            "medusa", "error", "Could not find source files related to this issue."
        )
        return
    root_cause = _own_root_cause(store, issue, "granite")

    bob_result = _investigation(store, issue, "bob")
    if bob_result is None:
        bob_result = await panel.bob_general_only(issue, files, ch)
    elif not bob_result.ok:
        await ch.emit("bob", "warn", f"Bob has no fixes to offer: {bob_result.error}")
    bob_patches = (
        [f for f in bob_result.candidate_fixes if f.patch] if bob_result.ok else []
    )
    n_bob = min(len(bob_patches), math.ceil(len(run.session.candidates) / 2))
    for candidate, fix in zip(
        run.session.candidates[:n_bob], bob_patches, strict=False
    ):
        candidate.approach, candidate.patch, candidate.origin = (
            fix.approach,
            fix.patch,
            "bob",
        )
        candidate.patch_stats = optilearn.patch_stats(fix.patch or "")
        await ch.emit(
            f"candidate:{candidate.candidate_id}",
            "result",
            "Proposed by Bob (not tested)",
        )

    async def one(candidate: FixAttempt) -> None:
        tag = f"candidate:{candidate.candidate_id}"
        await ch.emit(tag, "info", f"Proposing: {candidate.approach}")
        try:
            cand = await diff_candidate(issue, root_cause, files, candidate.approach)
        except GraniteError as exc:
            candidate.error = str(exc)
            await ch.emit(tag, "error", str(exc))
            return
        candidate.approach, candidate.patch, candidate.origin = (
            cand.approach,
            cand.patch,
            "granite",
        )
        candidate.patch_stats = optilearn.patch_stats(cand.patch)
        await ch.emit(tag, "result", f"Proposed (not tested): {cand.explanation}")

    await asyncio.gather(*(one(c) for c in run.session.candidates[n_bob:]))
    run.recommendation = verify.recommend_unverified(run.session.candidates)
    if run.recommendation:
        await ch.emit("recommendation", "result", run.recommendation.reason)
    else:
        await ch.emit("recommendation", "warn", "No patch could be proposed.")
