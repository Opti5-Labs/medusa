"""
The investigator panel: Bob and Granite, run independently and in parallel.

Each investigator gets the same inputs (the issue, the relevant code and, on the
demo path, the sandbox's runtime evidence) and never sees the other's
diagnosis. Both return an InvestigatorResult. Nothing here decides which patch
wins: the sandbox and pipelines/verify.py do that.
"""

import asyncio

from app import config
from app.agents import bob, granite
from app.agents.investigators import diagnose_general, format_sources, investigate_demo
from app.agents.results import InvestigatorResult, failed, unavailable
from app.models.contracts import InvestigatorSource, Issue
from app.streaming import EventChannel

_JSON_REPLY = (
    "Reply with ONLY one JSON object and nothing else, with these fields: "
    '"root_cause" (string), "evidence" (list of short file:line facts), '
    '"confidence" (0 to 1), "candidate_fixes" (list).'
)


def _issue_text(issue: Issue) -> str:
    where = issue.file or "unknown file"
    if issue.function:
        where += f" :: {issue.function}()"
    return f"Title: {issue.title}\nLocation: {where}\nReport: {issue.description}"


def demo_prompt(
    issue: Issue, files: dict[str, str], evidence: str, target_function: str
) -> str:
    return (
        "You are an independent investigator on a debugging team. Diagnose the bug "
        "below. The relevant files are in your workspace and quoted here; read more "
        "only if you need to. Do not edit files or run commands.\n\n"
        f"Bug report:\n{_issue_text(issue)}\n\n"
        f"Runtime evidence (observed in an isolated sandbox, treat as fact):\n{evidence}\n\n"
        f"Source code:\n{format_sources(files)}\n\n"
        "Trace the failing input through the code to the line that returns the bad "
        "value, then propose 1 or 2 fixes. Each fix must make the failing test pass, "
        "keep existing behaviour for valid inputs, and work offline. "
        f"{_JSON_REPLY} Each candidate fix is "
        f'{{"approach": "one sentence", "function_source": "the complete replacement '
        f'of def {target_function}(...), same signature, 4-space indentation"}}.'
    )


def general_prompt(issue: Issue, files: dict[str, str]) -> str:
    return (
        "You are an independent investigator on a debugging team. Diagnose the "
        "reported issue by reading the code only; nothing can be executed. The "
        "relevant files are in your workspace and quoted here. Do not edit files or "
        "run commands.\n\n"
        f"Issue:\n{_issue_text(issue)}\n\nSource code:\n{format_sources(files)}\n\n"
        f"Cite exact file:line evidence. {_JSON_REPLY} Each candidate fix is "
        '{"approach": "one sentence", "patch": "a unified diff with ---/+++ headers"}. '
        "Propose at most 2 fixes."
    )


async def _announce(channel: EventChannel, result: InvestigatorResult) -> None:
    """Surface the investigator's outcome, including the real failure reason."""
    name = "Bob" if result.investigator == "bob" else "Granite"
    src = result.investigator
    if result.status == "ok":
        cost = f", {result.cost} Bobcoins" if result.cost is not None else ""
        conf = (
            f" (self-reported confidence {result.confidence:.2f})"
            if result.confidence is not None
            else ""
        )
        if result.investigator == "bob":  # Granite streams its own evidence as it goes
            for item in result.evidence[:4]:
                await channel.emit(src, "info", f"Evidence: {item}")
        if result.candidate_fixes:
            await channel.emit(
                src, "info", f"{name} proposed {len(result.candidate_fixes)} fix(es)"
            )
        await channel.emit(
            src, "result", f"{name} root cause: {result.root_cause}{conf}{cost}"
        )
    elif result.status == "unavailable":
        await channel.emit(src, "warn", f"{name} unavailable: {result.error}")
    elif result.status == "limit":
        await channel.emit(src, "warn", f"{name} stopped at a limit: {result.error}")
    else:
        await channel.emit(src, "error", f"{name} failed: {result.error}")


async def _bob_live(
    prompt: str, files: dict[str, str], channel: EventChannel
) -> InvestigatorResult:
    if (reason := bob.live_unavailable_reason()) is None:
        await channel.emit(
            "bob",
            "info",
            f"Bob investigating (read-only; limits {config.BOB_MAX_COST} Bobcoins, "
            f"{config.BOB_MAX_TURNS} turns)",
        )
        result = await bob.investigate(prompt, files)
    else:
        result = unavailable("bob", reason)
    await _announce(channel, result)
    return result


async def _bob_side(
    prompt: str, files: dict[str, str], channel: EventChannel, allow_replay: bool
) -> InvestigatorResult:
    if allow_replay and config.BOB_MODE == "replay" and bob.golden_available():
        await channel.emit("bob", "info", "Replaying the recorded Bob session")
        result = await bob.replay_investigation(channel) or failed(
            "bob", "the recorded session could not be read."
        )
        await _announce(channel, result)
        return result
    return await _bob_live(prompt, files, channel)


def _granite_failure(exc: granite.GraniteError) -> InvestigatorResult:
    if not exc.retryable:  # not authorised, or token quota used up
        return unavailable("granite", str(exc))
    return failed("granite", str(exc))


async def _granite_demo(
    issue: Issue, files: dict[str, str], evidence: str, channel: EventChannel
) -> InvestigatorResult:
    if not granite.is_configured():
        result = unavailable("granite", "Granite is not configured on this server.")
    else:
        try:
            synth = await investigate_demo(issue, files, channel, evidence)
            result = InvestigatorResult(
                investigator="granite",
                status="ok",
                root_cause=synth.root_cause,
                evidence=synth.evidence,
                confidence=synth.confidence,
            )
        except granite.GraniteError as exc:
            result = _granite_failure(exc)
    await _announce(channel, result)
    return result


async def _granite_general(
    issue: Issue, files: dict[str, str], channel: EventChannel
) -> InvestigatorResult:
    if not granite.is_configured():
        result = unavailable("granite", "Granite is not configured on this server.")
    else:
        try:
            diag = await diagnose_general(issue, files, channel)
            result = InvestigatorResult(
                investigator="granite",
                status="ok",
                root_cause=diag.root_cause,
                evidence=[
                    f"{c.file}:{c.line} {c.explanation}"
                    if c.line
                    else f"{c.file} {c.explanation}"
                    for c in diag.citations
                ],
                confidence=diag.confidence,
            )
        except granite.GraniteError as exc:
            result = _granite_failure(exc)
    await _announce(channel, result)
    return result


async def investigate_demo_panel(
    issue: Issue,
    files: dict[str, str],
    evidence: str,
    target_function: str,
    channel: EventChannel,
) -> list[InvestigatorResult]:
    """Bob and Granite in parallel on the sandbox-reproduced demo bug."""
    prompt = demo_prompt(issue, files, evidence, target_function)
    bob_result, granite_result = await asyncio.gather(
        _bob_side(prompt, files, channel, allow_replay=True),
        _granite_demo(issue, files, evidence, channel),
    )
    return [bob_result, granite_result]


async def investigate_general_panel(
    issue: Issue, files: dict[str, str], channel: EventChannel
) -> list[InvestigatorResult]:
    """Bob and Granite in parallel on a general repo (text only, nothing executed)."""
    bob_result, granite_result = await asyncio.gather(
        _bob_live(general_prompt(issue, files), files, channel),
        _granite_general(issue, files, channel),
    )
    return [bob_result, granite_result]


async def bob_demo_only(
    issue: Issue,
    files: dict[str, str],
    evidence: str,
    target_function: str,
    channel: EventChannel,
) -> InvestigatorResult:
    """Bob alone, for a debug run that has no reproduce investigation to reuse."""
    return await _bob_side(
        demo_prompt(issue, files, evidence, target_function),
        files,
        channel,
        allow_replay=True,
    )


# ── Combining for display (never for ranking) ─────────────────────────────────


def source_of(results: list[InvestigatorResult]) -> InvestigatorSource:
    ok = {r.investigator for r in results if r.ok}
    if not ok:
        return "unavailable"
    if ok == {"bob", "granite"}:
        return "bob_and_granite"
    if ok == {"bob"}:
        return "bob_replay" if any(r.recorded for r in results) else "bob"
    return "granite"


def combined_root_cause(results: list[InvestigatorResult]) -> str | None:
    """Each successful diagnosis, labelled. Neither overrides the other."""
    ok = [r for r in results if r.ok]
    if not ok:
        return None
    if len(ok) == 1:
        return ok[0].root_cause
    names = {"bob": "Bob", "granite": "Granite"}
    return "\n\n".join(f"{names[r.investigator]}: {r.root_cause}" for r in ok)


def reported_confidence(results: list[InvestigatorResult]) -> float | None:
    """For reasoning mode's display: the most cautious self-reported confidence."""
    values = [r.confidence for r in results if r.ok and r.confidence is not None]
    return round(min(values), 2) if values else None


async def bob_general_only(
    issue: Issue, files: dict[str, str], channel: EventChannel
) -> InvestigatorResult:
    """Bob alone on a general repo, for a debug run with no reproduce investigation."""
    return await _bob_live(general_prompt(issue, files), files, channel)
