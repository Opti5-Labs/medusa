"""
Investigator agents on Granite.

OptiLearn demo (when no recorded Bob run exists):
    runtime + repository investigators in parallel → skeptic → synthesis
General repos:
    one reasoning call → root-cause hypothesis with file/line citations

Each step streams its findings as LogEvents. Diagnosis only: no agent here
proposes or applies code changes.
"""

import asyncio

from pydantic import Field

from app.agents.granite import LenientModel, chat_json
from app.models.contracts import Issue
from app.streaming import EventChannel

_JSON_ONLY = "Respond with a single JSON object only. No prose, no code fences."
_MAX_CONTEXT_CHARS = 24_000


class RuntimeFinding(LenientModel):
    observed_failure: str
    expected_behaviour: str
    trigger_conditions: str
    likely_location: str
    confidence: float = Field(ge=0, le=1)


class RepositoryFinding(LenientModel):
    suspected_function: str
    # Written first: a step-by-step walk of the failing input through the code.
    execution_trace: list[str] = Field(default_factory=list)
    code_evidence: list[str]
    root_cause_hypothesis: str
    confidence: float = Field(ge=0, le=1)


class SkepticReview(LenientModel):
    confirmed_claims: list[str]
    rejected_claims: list[str]
    open_questions: list[str]
    alternative_hypothesis: str | None = None


class Synthesis(LenientModel):
    root_cause: str
    evidence: list[str]
    confidence: float = Field(ge=0, le=1)


class Citation(LenientModel):
    file: str
    line: int | None = None
    explanation: str


class ReasoningDiagnosis(LenientModel):
    root_cause: str
    citations: list[Citation]
    reproduction_steps: list[str]
    confidence: float = Field(ge=0, le=1)


def format_sources(files: dict[str, str]) -> str:
    """
    Number every line so the model can cite file:line. A key like
    "path#L66" marks an excerpt whose first line is line 66 of the file.
    """
    parts: list[str] = []
    used = 0
    for key, text in files.items():
        path, _, first = key.partition("#L")
        start = int(first) if first.isdigit() else 1
        numbered = "\n".join(
            f"{i:>4} | {line}" for i, line in enumerate(text.splitlines(), start)
        )
        label = f"{path} (excerpt)" if start > 1 else path
        block = f"### {label}\n{numbered}\n"
        if used + len(block) > _MAX_CONTEXT_CHARS:
            block = block[: max(0, _MAX_CONTEXT_CHARS - used)] + "\n[truncated]\n"
        parts.append(block)
        used += len(block)
        if used >= _MAX_CONTEXT_CHARS:
            break
    return "\n".join(parts)


def _issue_text(issue: Issue) -> str:
    location = f"{issue.file or 'unknown file'}"
    if issue.function:
        location += f" :: {issue.function}()"
    return f"Title: {issue.title}\nLocation: {location}\nReport: {issue.description}"


async def investigate_demo(
    issue: Issue,
    files: dict[str, str],
    channel: EventChannel,
    runtime_evidence: str | None = None,
) -> tuple[Synthesis, RuntimeFinding, RepositoryFinding]:
    """
    Run the four-step investigation, streaming each finding. Raises GraniteError.

    *runtime_evidence* is what actually happened in the sandbox (the failing
    test, its source and its assertion output). Findings must be consistent
    with it. Returns the synthesis plus the two findings it was built from,
    so callers can show the trigger conditions and execution trace, not just
    the final root cause.
    """
    sources = format_sources(files)
    context = f"Bug report:\n{_issue_text(issue)}\n\nSource code:\n{sources}"
    if runtime_evidence:
        context += (
            "\n\nRuntime evidence (observed in the sandbox, treat as fact):\n"
            f"{runtime_evidence}"
        )

    await channel.emit("investigator:runtime", "info", "Runtime investigator started")
    await channel.emit(
        "investigator:repository", "info", "Repository investigator started"
    )
    runtime, repository = await asyncio.gather(
        chat_json(
            "You are the runtime investigator on a debugging team. Determine what fails "
            "and under which conditions. Start from the runtime evidence: the exact input "
            "the failing test used and the value the code returned. "
            "Fields: observed_failure, expected_behaviour, trigger_conditions, "
            "likely_location (file:line), confidence (0-1). " + _JSON_ONLY,
            context,
            RuntimeFinding,
        ),
        chat_json(
            "You are the repository investigator on a debugging team. Determine where the "
            "failure originates in the code. First fill execution_trace: take the exact "
            "failing input from the runtime evidence and step through the suspected "
            "function, stating for each condition whether it is true or false for that "
            "input, until the line that returns the bad value. At most 8 steps, each one "
            'short line like "L12 if user is None: True". Then base code_evidence and root_cause_hypothesis only on '
            "lines the trace actually reaches. Quote code as evidence with file:line. "
            "Fields, in this order: suspected_function, execution_trace (list), "
            "code_evidence (list), root_cause_hypothesis, confidence (0-1). "
            + _JSON_ONLY,
            context,
            RepositoryFinding,
            max_tokens=2500,
        ),
    )
    await channel.emit(
        "investigator:runtime", "info", f"Observed failure: {runtime.observed_failure}"
    )
    await channel.emit(
        "investigator:runtime", "info", f"Trigger: {runtime.trigger_conditions}"
    )
    await channel.emit(
        "investigator:runtime",
        "result",
        f"Likely location: {runtime.likely_location} (confidence {runtime.confidence:.2f})",
    )
    for step in repository.execution_trace[:8]:
        await channel.emit("investigator:repository", "info", f"Trace: {step}")
    for item in repository.code_evidence[:4]:
        await channel.emit("investigator:repository", "info", f"Evidence: {item}")
    await channel.emit(
        "investigator:repository",
        "result",
        f"Hypothesis: {repository.root_cause_hypothesis} (confidence {repository.confidence:.2f})",
    )

    await channel.emit(
        "investigator:skeptic", "info", "Skeptic reviewing both hypotheses"
    )
    skeptic = await chat_json(
        "You are the skeptic on a debugging team. Challenge the two findings below: is "
        "each claim supported by the code, and consistent with the runtime evidence (the "
        "input used and the value returned)? Reject claims about code paths the failing "
        "input never reaches. Could something else cause the same failure? "
        "Fields: confirmed_claims, rejected_claims, open_questions (lists of strings), "
        "alternative_hypothesis (string or null). " + _JSON_ONLY,
        f"{context}\n\nRuntime finding:\n{runtime.model_dump_json()}\n\n"
        f"Repository finding:\n{repository.model_dump_json()}",
        SkepticReview,
    )
    for claim in skeptic.confirmed_claims[:3]:
        await channel.emit("investigator:skeptic", "info", f"Confirmed: {claim}")
    for claim in skeptic.rejected_claims[:3]:
        await channel.emit("investigator:skeptic", "warn", f"Rejected: {claim}")
    for question in skeptic.open_questions[:2]:
        await channel.emit("investigator:skeptic", "info", f"Open question: {question}")
    if skeptic.alternative_hypothesis:
        await channel.emit(
            "investigator:skeptic",
            "info",
            f"Alternative: {skeptic.alternative_hypothesis}",
        )

    await channel.emit("synthesis", "info", "Synthesising root cause")
    synthesis = await chat_json(
        "You combine a debugging team's findings into one diagnosis. Keep only claims the "
        "skeptic did not reject. The root cause must explain the runtime evidence "
        "exactly: which input reaches which line and why the result is wrong. Do not propose a fix. Fields: root_cause, evidence "
        "(list of file:line facts), confidence (0-1). " + _JSON_ONLY,
        f"{context}\n\nRuntime:\n{runtime.model_dump_json()}\n\nRepository:\n"
        f"{repository.model_dump_json()}\n\nSkeptic:\n{skeptic.model_dump_json()}",
        Synthesis,
    )
    await channel.emit("synthesis", "result", f"Root cause: {synthesis.root_cause}")
    return synthesis, runtime, repository


async def diagnose_general(
    issue: Issue, files: dict[str, str], channel: EventChannel
) -> ReasoningDiagnosis:
    """One reasoning call for a general repo. Code is never executed. Raises GraniteError."""
    await channel.emit(
        "granite", "info", f"Analysing {len(files)} file(s) as text (not executed)"
    )
    diagnosis = await chat_json(
        "You are a senior engineer diagnosing a reported issue by reading code only; "
        "nothing can be executed. Explain the most likely root cause and cite the exact "
        "file and line numbers shown. Give the steps a developer would follow to "
        "reproduce it. Be honest about uncertainty in confidence (0-1). Fields: "
        "root_cause, citations (list of {file, line, explanation}), reproduction_steps "
        "(list), confidence. " + _JSON_ONLY,
        f"Issue:\n{_issue_text(issue)}\n\nSource code:\n{format_sources(files)}",
        ReasoningDiagnosis,
    )
    for cite in diagnosis.citations[:6]:
        where = f"{cite.file}:{cite.line}" if cite.line else cite.file
        await channel.emit("granite", "info", f"{where} — {cite.explanation}")
    for step in diagnosis.reproduction_steps[:5]:
        await channel.emit("granite", "info", f"To reproduce: {step}")
    await channel.emit(
        "granite", "result", f"Likely root cause: {diagnosis.root_cause}"
    )
    return diagnosis
