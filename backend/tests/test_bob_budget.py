"""Server-wide daily Bobcoin budget (agents/bob_budget.py) and its use in bob.ask."""

import json

import pytest

from app.agents import bob, bob_budget
from app.agents.bob_budget import DailyBudget


@pytest.fixture
def limits(monkeypatch):
    def set_limits(daily: float, per_run: float = 0.25):
        monkeypatch.setattr("app.config.BOB_DAILY_BUDGET", daily)
        monkeypatch.setattr("app.config.BOB_MAX_COST", per_run)

    return set_limits


def test_reservations_never_overshoot_the_budget(limits):
    limits(daily=0.6, per_run=0.25)
    b = DailyBudget(None)
    first, second = b.reserve(), b.reserve()
    assert first and second
    assert b.reserve() is None  # 0.75 would exceed 0.6
    assert "used up" in b.exhausted_reason()


def test_settling_frees_the_unused_part_of_a_reservation(limits):
    limits(daily=0.6, per_run=0.25)
    b = DailyBudget(None)
    r1, r2 = b.reserve(), b.reserve()
    b.settle(r1, 0.03)
    b.settle(r2, 0.04)
    assert b.spent_today() == 0.07
    assert b.reserve() is not None  # 0.07 + 0.25 fits again


def test_zero_budget_disables_the_cap(limits):
    limits(daily=0)
    b = DailyBudget(None)
    assert all(b.reserve() is not None for _ in range(100))
    assert b.exhausted_reason() is None


def test_spend_survives_a_restart_on_the_same_day(tmp_path, limits):
    limits(daily=1.0)
    path = tmp_path / "budget.json"
    b = DailyBudget(path)
    b.settle(b.reserve(), 0.8)
    again = DailyBudget(path)
    assert again.spent_today() == 0.8
    assert again.reserve() is None  # 0.8 + 0.25 > 1.0


def test_a_new_day_starts_from_zero(tmp_path, limits):
    limits(daily=1.0)
    path = tmp_path / "budget.json"
    path.write_text(json.dumps({"day": "2000-01-01", "spent": 0.99}))
    assert DailyBudget(path).spent_today() == 0.0


def test_corrupt_budget_file_starts_from_zero(tmp_path, limits):
    limits(daily=1.0)
    path = tmp_path / "budget.json"
    path.write_text("{not json")
    assert DailyBudget(path).spent_today() == 0.0


# ── Through bob.ask ───────────────────────────────────────────────────────────


class _Answer(bob.BaseModel):
    answer: str


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr("app.config.BOB_MODE", "live")
    monkeypatch.setattr("app.config.BOB_API_KEY", "bob-test-key-0123456789abcdef")
    monkeypatch.setattr(bob.shutil, "which", lambda name: "/usr/bin/bob")
    monkeypatch.setattr(bob, "_cache", bob.OrderedDict())


async def test_exhausted_budget_blocks_bob_with_a_clear_reason(
    live, limits, monkeypatch
):
    limits(daily=0.3, per_run=0.25)
    bob_budget.budget().settle(bob_budget.budget().reserve(), 0.2)

    async def must_not_run(*a, **kw):
        raise AssertionError("Bob must not start when the budget is used up")

    monkeypatch.setattr(bob, "_run_cli", must_not_run)
    answer = await bob.ask("q", {}, _Answer)
    assert answer.status == "unavailable"
    assert "budget" in answer.error and "00:00 UTC" in answer.error
    assert "budget" in (bob.live_unavailable_reason() or "")


async def test_runs_are_charged_their_real_cost(live, limits, monkeypatch):
    limits(daily=5.0)

    async def fake_run(prompt, files, schema, timeout_s, text_field=None):
        return bob.BobAnswer("ok", data=schema(answer="a"), cost=0.031)

    monkeypatch.setattr(bob, "_run_cli", fake_run)
    await bob.ask("q1", {}, _Answer)
    await bob.ask("q1", {}, _Answer)  # cached: free
    assert bob_budget.budget().spent_today() == 0.031


@pytest.mark.parametrize(
    ("answer", "charged"),
    [
        (bob.BobAnswer("limit", error="Bob did not finish within 180 s"), 0.25),
        (bob.BobAnswer("error", error="Bob authentication failed: bad key"), 0.0),
    ],
    ids=["timeout-charges-worst-case", "never-started-costs-nothing"],
)
async def test_unknown_cost_is_charged_by_outcome(
    live, limits, monkeypatch, answer, charged
):
    limits(daily=5.0, per_run=0.25)

    async def fake_run(prompt, files, schema, timeout_s, text_field=None):
        return answer

    monkeypatch.setattr(bob, "_run_cli", fake_run)
    await bob.ask("q", {}, _Answer)
    assert bob_budget.budget().spent_today() == charged
