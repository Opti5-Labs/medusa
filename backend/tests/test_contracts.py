"""Contract round-trips and the event channel."""

import asyncio
import json
import time
from pathlib import Path

import pytest

from app import store as store_mod
from app.models.contracts import (
    AskAnswer,
    AskCitation,
    AskStart,
    AskStatus,
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
from app.store import AskRun, RunStore, ScanRecord, StoreFullError
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
    asks = [
        AskAnswer(
            ask_id="q1",
            question="Where is auth?",
            answer="In `auth.py`.",
            grounding="reasoning",
            answered_by="granite",
            citations=[AskCitation(file="auth.py", line=12), AskCitation(file="b.py")],
            files_read=["auth.py", "b.py"],
            issue_id="i",
        ),
        AskAnswer(
            ask_id="q2",
            question="Why?",
            answer="Because.",
            grounding="reasoning",
            answered_by="bob",
            cost=0.05,
            notice="Granite was unavailable, so Bob answered.",
        ),
        AskAnswer(
            ask_id="q3",
            question="?",
            answer="",
            grounding="reasoning",
            answered_by="granite",
            error="No model could answer.",
        ),
        AskStart(ask_id="q1"),
        AskStatus(enabled=True, granite_available=True, bob_available=False),
        AskStatus(
            enabled=True,
            granite_available=False,
            bob_available=False,
            bob_reason="no key",
        ),
    ]
    for model in (scan, done, attempt, *asks):
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


async def test_token_events_replay_in_order_and_stay_out_of_the_log():
    ch = EventChannel()
    await ch.token("Hel")
    await ch.token('lo "x"')
    await ch.done("{}")
    events = [item async for item in ch.stream()]
    assert [name for name, _ in events] == ["token", "token", "done"]
    assert [json.loads(data)["text"] for name, data in events[:2]] == ["Hel", 'lo "x"']
    assert ch.log == []


def _ask_run(ask_id: str, scan_id: str = "s") -> AskRun:
    return AskRun(ask_id=ask_id, scan_id=scan_id, channel=EventChannel())


async def test_store_sweeps_expired_ask_runs_and_cancels_their_task():
    store = RunStore()
    run = _ask_run("a1")
    run.task = asyncio.create_task(asyncio.sleep(60))
    run.created_at = time.monotonic() - store_mod.RUN_TTL_SECONDS - 1
    fresh = _ask_run("a2")
    store.add_ask(run)
    store.add_ask(fresh)
    await store._sweep_once()
    assert list(store.ask_runs) == ["a2"]
    with pytest.raises(asyncio.CancelledError):
        await run.task


def test_add_ask_past_the_cap_raises():
    store = RunStore()
    for i in range(store_mod._MAX_ASK_RUNS):
        store.add_ask(_ask_run(f"a{i}"))
    with pytest.raises(StoreFullError):
        store.add_ask(_ask_run("one-too-many"))


async def test_ask_running_for_is_true_until_the_channel_is_done():
    store = RunStore()
    run = _ask_run("a1", scan_id="s1")
    store.add_ask(run)
    assert store.ask_running_for("s1")
    assert not store.ask_running_for("other")
    await run.channel.done("{}")
    assert not store.ask_running_for("s1")


async def test_stop_cancels_running_ask_tasks():
    store = RunStore()
    run = _ask_run("a1")
    run.task = asyncio.create_task(asyncio.sleep(60))
    store.add_ask(run)
    await store.stop()
    with pytest.raises(asyncio.CancelledError):
        await run.task


def test_scan_record_remember_keeps_the_last_20():
    record = ScanRecord(scan_id="s", tmp_dir=None, result=None)  # type: ignore[arg-type]
    for i in range(25):
        record.remember(f"q{i}", f"a{i}")
    assert len(record.history) == 20
    assert record.history[0] == ("q5", "a5")
    assert record.history[-1] == ("q24", "a24")


def test_ask_contracts_are_mirrored_in_the_frontend_types():
    api_ts = Path(__file__).resolve().parents[2] / "frontend" / "lib" / "api.ts"
    text = api_ts.read_text("utf-8")
    for model in (AskAnswer, AskCitation, AskStart, AskStatus):
        assert f"interface {model.__name__}" in text
        for name in model.model_fields:
            assert name in text, f"{model.__name__}.{name} missing from api.ts"
