"""Tests for deterministic verification and ranking."""

from app.models.contracts import FixAttempt, PatchStats, TestResults
from app.pipelines import verify
from app.sandbox.runner import GroupResult, SandboxResult


def _run(
    repro_failed: int, suite_failures: list[str], suite_total: int = 6
) -> SandboxResult:
    return SandboxResult(
        ok=True,
        reproducer=GroupResult(
            1 - repro_failed, repro_failed, 1, ["repro"] if repro_failed else []
        ),
        suite=GroupResult(
            suite_total - len(suite_failures),
            len(suite_failures),
            suite_total,
            suite_failures,
        ),
    )


BASELINE = _run(repro_failed=1, suite_failures=[])


def test_fixed_candidate_passes():
    r = verify.evaluate(BASELINE, _run(0, []))
    assert r.reproducer_fixed and not r.regressions
    assert (r.passed, r.total) == (7, 7)
    assert verify.is_passing(r)


def test_regression_detected():
    r = verify.evaluate(BASELINE, _run(0, ["test_custom_hub_id"]))
    assert r.regressions == ["test_custom_hub_id"]
    assert not verify.is_passing(r)


def test_preexisting_failures_are_not_regressions():
    baseline = _run(1, ["flaky"])
    r = verify.evaluate(baseline, _run(0, ["flaky"]))
    assert r.regressions == []


def test_reproducer_still_failing():
    r = verify.evaluate(BASELINE, _run(1, []))
    assert not r.reproducer_fixed
    assert not verify.is_passing(r)


def _cand(cid: str, status: str, passed: int, size: tuple[int, int]) -> FixAttempt:
    return FixAttempt(
        candidate_id=cid,
        approach="x",
        patch="diff",
        sandbox_status=status,
        test_results=TestResults(
            passed=passed,
            failed=7 - passed,
            total=7,
            reproducer_fixed=status == "passed",
        ),
        patch_stats=PatchStats(
            files_changed=1, lines_added=size[0], lines_removed=size[1]
        ),
    )


def test_recommends_smallest_passing_patch():
    cands = [
        _cand("c1", "passed", 7, (10, 2)),
        _cand("c2", "passed", 7, (3, 1)),
        _cand("c3", "failed", 6, (1, 1)),
    ]
    rec = verify.recommend_verified(cands)
    assert rec.candidate_id == "c2"
    assert rec.verified
    assert "1 other candidate failed" in rec.reason


def test_no_recommendation_when_nothing_passes():
    assert verify.recommend_verified([_cand("c1", "failed", 6, (1, 1))]) is None


def test_unverified_recommendation_is_labelled():
    cands = [
        FixAttempt(
            candidate_id="c1",
            approach="a",
            patch="+x\n+y",
            sandbox_status="not_applicable",
            patch_stats=PatchStats(files_changed=1, lines_added=2, lines_removed=0),
        ),
        FixAttempt(
            candidate_id="c2", approach="b", patch=None, sandbox_status="not_applicable"
        ),
    ]
    rec = verify.recommend_unverified(cands)
    assert rec.candidate_id == "c1"
    assert not rec.verified
    assert "Not verified" in rec.reason
