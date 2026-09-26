"""Contract round-trips and the event channel."""

import asyncio

from app.models.contracts import (
    DebugDone,
    DebugSession,
    FixAttempt,
    Issue,
    PatchStats,
    Recommendation,
    ReproAttempt,
    ScanResult,
    TestResults,
)
from app.streaming import EventChannel


def test_contracts_round_trip():
    issue = Issue(
        id="i", title="t", description="d", priority="High", source="scan", line=3
    )
    scan = ScanResult(
        scan_id="s",
        repo_source="zip",
        language="Python",
        files_scanned=["a.py"],
        files_total=1,
        issues=[issue],
    )
    fix = FixAttempt(
        candidate_id="c1",
        approach="a",
        patch="+x",
        sandbox_status="passed",
        origin="granite",
        test_results=TestResults(passed=7, failed=0, total=7, reproducer_fixed=True),
        patch_stats=PatchStats(files_changed=1, lines_added=1, lines_removed=0),
    )
    done = DebugDone(
        session=DebugSession(
            session_id="x", issue_id="i", mode="sandboxed", candidates=[fix]
        ),
        recommendation=Recommendation(candidate_id="c1", reason="r", verified=True),
    )
    attempt = ReproAttempt(
        attempt_id="a",
        issue_id="i",
        mode="reasoning",
        status="plausible",
        confidence=0.6,
        investigator_source="granite",
    )
    for model in (scan, done, attempt):
        assert type(model).model_validate_json(model.model_dump_json()) == model


async def test_channel_replays_history_to_late_subscribers():
    ch = EventChannel()
    await ch.emit("sandbox", "info", "one")

    async def collect():
        return [name async for name, _ in ch.stream()]

    early = asyncio.create_task(collect())
    await asyncio.sleep(0)
    await ch.emit("sandbox", "result", "two")
    await ch.done("{}")
    late = await collect()
    assert await early == late == ["log", "log", "done"]
    assert [e.message for e in ch.log] == ["one", "two"]
