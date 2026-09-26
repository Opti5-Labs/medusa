"""
Server-wide daily Bobcoin budget for live Bob runs.

Per-run caps (BOB_MAX_COST) and per-IP rate limits do not bound total spend on
a public site, so every live Bob run also draws from one daily budget
(BOB_DAILY_BUDGET Bobcoins per UTC day):

    reservation = budget.reserve()      # None when today's budget is used up
    ...run Bob...
    budget.settle(reservation, cost)    # replace the reservation with the real cost

A run reserves the worst case (BOB_MAX_COST) before it starts, so concurrent
runs can never overshoot the budget; settling swaps in the reported cost. The
day's spend is saved to BOB_BUDGET_FILE so restarts and deploys don't reset it.
Cached answers never reach this module, so they are free.
"""

import json
import logging
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

from app import config

log = logging.getLogger(__name__)


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


class DailyBudget:
    def __init__(self, path: Path | None) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._day = _today()
        self._spent = 0.0
        self._reserved: dict[int, float] = {}
        self._next_id = 0
        self._load()

    # ── Persistence ────────────────────────────────────────────────────────────

    def _load(self) -> None:
        if self._path is None or not self._path.is_file():
            return
        try:
            data = json.loads(self._path.read_text("utf-8"))
            if data.get("day") == self._day:
                self._spent = max(0.0, float(data.get("spent", 0.0)))
        except (OSError, ValueError, TypeError):
            log.warning("bob budget: could not read %s; starting from 0", self._path)

    def _save(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps({"day": self._day, "spent": round(self._spent, 4)}), "utf-8"
            )
            os.replace(tmp, self._path)
        except OSError as exc:
            log.warning("bob budget: could not save %s: %s", self._path, exc)

    def _roll_day(self) -> None:
        today = _today()
        if today != self._day:
            self._day, self._spent = today, 0.0
            self._save()

    # ── Accounting ─────────────────────────────────────────────────────────────

    def committed(self) -> float:
        """Spent today plus everything reserved by runs still in progress."""
        with self._lock:
            self._roll_day()
            return self._spent + sum(self._reserved.values())

    def exhausted_reason(self) -> str | None:
        """A user-facing reason when another run could exceed today's budget."""
        limit = config.BOB_DAILY_BUDGET
        if limit <= 0:
            return None  # budget disabled
        if self.committed() + config.BOB_MAX_COST > limit + 1e-9:
            return (
                f"today's Bob budget ({limit:g} Bobcoins) is used up on this server; "
                "it resets at 00:00 UTC."
            )
        return None

    def reserve(self) -> int | None:
        """Reserve BOB_MAX_COST for one run. None when the budget can't cover it."""
        limit = config.BOB_DAILY_BUDGET
        with self._lock:
            self._roll_day()
            committed = self._spent + sum(self._reserved.values())
            if limit > 0 and committed + config.BOB_MAX_COST > limit + 1e-9:
                return None
            self._next_id += 1
            self._reserved[self._next_id] = config.BOB_MAX_COST
            return self._next_id

    def settle(self, reservation: int, cost: float) -> None:
        """Replace a reservation with the run's real cost."""
        with self._lock:
            self._reserved.pop(reservation, None)
            actual = max(0.0, float(cost))
            self._roll_day()
            self._spent += actual
            self._save()
        log.info(
            "bob budget: run cost %.4f, %.4f of %g spent today",
            actual,
            self._spent,
            config.BOB_DAILY_BUDGET,
        )

    def spent_today(self) -> float:
        with self._lock:
            self._roll_day()
            return round(self._spent, 4)


_budget: DailyBudget | None = None


def budget() -> DailyBudget:
    """The process-wide budget (created on first use, from config)."""
    global _budget
    if _budget is None:
        path = (
            Path(config.BOB_BUDGET_FILE).expanduser()
            if config.BOB_BUDGET_FILE
            else None
        )
        _budget = DailyBudget(path)
    return _budget


def reset_for_tests() -> None:
    global _budget
    _budget = None
