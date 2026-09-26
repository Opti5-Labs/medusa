"""
Deterministic verification and ranking. No LLM decides whether a patch works.

    results = evaluate(baseline, candidate_run)   # original vs patched sandbox runs
    results = evaluate_exec(baseline, candidate)  # the same for general repos (pyexec)
    rec = recommend_verified(candidates)          # sandboxed mode
    rec = recommend_unverified(candidates)        # reasoning mode, clearly labelled
"""

from app.models.contracts import FixAttempt, Recommendation, TestResults
from app.sandbox.pyexec import TestRun
from app.sandbox.runner import SandboxResult


def evaluate(baseline: SandboxResult, patched: SandboxResult) -> TestResults:
    """Compare a patched run with the original run. Both must have ok=True."""
    assert patched.reproducer and patched.suite
    before_failures = set(baseline.suite.failures) if baseline.suite else set()
    regressions = [f for f in patched.suite.failures if f not in before_failures]
    return TestResults(
        passed=patched.reproducer.passed + patched.suite.passed,
        failed=patched.reproducer.failed + patched.suite.failed,
        total=patched.reproducer.total + patched.suite.total,
        reproducer_fixed=patched.reproducer_passed,
        regressions=regressions,
    )


def evaluate_exec(baseline: TestRun, patched: TestRun) -> TestResults:
    """
    Compare a patched general-repo run with the original. A test module that
    stops importing is a regression too: its tests vanish rather than fail.
    """
    before_failures = set(baseline.suite.failures)
    before_broken = set(baseline.collection_errors)
    regressions = [f for f in patched.suite.failures if f not in before_failures]
    regressions += [
        f"{e} (no longer imports)"
        for e in patched.collection_errors
        if e not in before_broken and "test_medusa_repro" not in e
    ]
    r, s = patched.reproducer, patched.suite
    return TestResults(
        passed=r.passed + s.passed,
        failed=r.failed + r.errors + s.failed + s.errors,
        total=r.total + s.total,
        reproducer_fixed=patched.repro_outcome == "passed",
        regressions=regressions,
    )


def is_passing(results: TestResults) -> bool:
    return results.reproducer_fixed and not results.regressions


def _patch_size(c: FixAttempt) -> int:
    return (
        (c.patch_stats.lines_added + c.patch_stats.lines_removed)
        if c.patch_stats
        else 10**6
    )


def recommend_verified(candidates: list[FixAttempt]) -> Recommendation | None:
    """Pick the passing candidate with the most checks passed, then the smallest patch."""
    eligible = [
        c for c in candidates if c.sandbox_status == "passed" and c.test_results
    ]
    if not eligible:
        return None
    eligible.sort(
        key=lambda c: (-c.test_results.passed, _patch_size(c), c.candidate_id)
    )
    best = eligible[0]
    r, s = best.test_results, best.patch_stats
    reason = (
        f"{best.candidate_id} fixes the reproducer and passes {r.passed}/{r.total} checks "
        f"with no regressions"
    )
    if s:
        reason += f", with a {s.lines_added + s.lines_removed}-line change (+{s.lines_added} −{s.lines_removed})"
    others = len(eligible) - 1
    failed = sum(1 for c in candidates if c.sandbox_status == "failed")
    if others:
        reason += (
            f". It ranks first of {len(eligible)} passing candidates "
            "(most checks passed, then smallest change)"
        )
    if failed:
        reason += f"; {failed} other candidate{'s' if failed != 1 else ''} failed verification"
    return Recommendation(
        candidate_id=best.candidate_id, reason=reason + ".", verified=True
    )


def recommend_unverified(candidates: list[FixAttempt]) -> Recommendation | None:
    """Reasoning mode: nothing ran, so prefer the smallest proposed patch and say so."""
    proposed = [c for c in candidates if c.patch]
    if not proposed:
        return None
    proposed.sort(key=lambda c: (_patch_size(c), c.candidate_id))
    best = proposed[0]
    return Recommendation(
        candidate_id=best.candidate_id,
        reason=(
            f"{best.candidate_id} is the smallest proposed change. Not verified: the code "
            "was not executed, so review and test the patch before using it."
        ),
        verified=False,
    )
