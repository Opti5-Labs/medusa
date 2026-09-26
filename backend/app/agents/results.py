"""
Normalised investigator results.

Every investigator (Bob, Granite) returns an InvestigatorResult, so the
pipelines never depend on which model produced a diagnosis. Confidence is
informational only: it is shown, never used to pick a patch.
"""

from typing import Literal

from pydantic import BaseModel

from app.agents.granite import LenientModel
from app.models.contracts import InvestigatorReport

InvestigatorName = Literal["bob", "granite"]
Status = Literal["ok", "unavailable", "error", "limit"]


class CandidateFix(LenientModel):
    """A fix proposed by an investigator. Exactly one of the two forms is set."""

    approach: str = "Fix proposed without a named strategy"
    function_source: str | None = None  # full replacement of the target function (demo)
    patch: str | None = None  # unified diff (general repos, shown only)


class InvestigatorResult(BaseModel):
    investigator: InvestigatorName
    status: Status
    root_cause: str | None = None
    evidence: list[str] = []
    confidence: float | None = None  # self-reported; never ranks patches
    candidate_fixes: list[CandidateFix] = []
    error: str | None = None  # why it is unavailable / failed / stopped at a limit
    cost: float | None = None  # Bobcoins spent (Bob only)
    recorded: bool = False  # replayed from a recorded session, not run live

    @property
    def ok(self) -> bool:
        return self.status == "ok" and bool(self.root_cause)

    def report(self) -> InvestigatorReport:
        return InvestigatorReport(
            investigator=self.investigator,
            status=self.status,
            root_cause=self.root_cause,
            evidence=self.evidence[:6],
            confidence=self.confidence,
            proposed_fixes=len(self.candidate_fixes),
            error=self.error,
            cost=self.cost,
            recorded=self.recorded,
        )


def unavailable(name: InvestigatorName, reason: str) -> InvestigatorResult:
    return InvestigatorResult(investigator=name, status="unavailable", error=reason)


def failed(
    name: InvestigatorName,
    reason: str,
    *,
    limit: bool = False,
    cost: float | None = None,
) -> InvestigatorResult:
    return InvestigatorResult(
        investigator=name, status="limit" if limit else "error", error=reason, cost=cost
    )
