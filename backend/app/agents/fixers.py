"""
Candidate fix generation on Granite. Each candidate follows a different strategy.

OptiLearn demo: Granite returns a full replacement for the target function,
which Medusa splices in and tests in the sandbox. A whole function is far more
reliable to apply than a model-written diff.

General repos: Granite returns a unified diff that is shown, never applied or run.
"""

from app.agents.granite import LenientModel, chat_json
from app.agents.investigators import format_sources
from app.models.contracts import Issue

_JSON_ONLY = "Respond with a single JSON object only. No prose, no code fences."

STRATEGIES: list[str] = [
    "Minimal local fix: the smallest change at the point of failure",
    "Input validation: validate the input before it is used",
    "Defensive handling: handle the bad case explicitly and log it",
    "Structural fix: restructure the logic so the bad case cannot arise",
    "Fallback to a safe default for the failing case only",
    "Normalise the input so every accepted form is handled",
]


class FunctionCandidate(LenientModel):
    approach: str
    function_source: str


class DiffCandidate(LenientModel):
    approach: str
    patch: str
    explanation: str


async def function_candidate(
    issue: Issue,
    root_cause: str,
    function_name: str,
    current_source: str,
    file_context: str,
    strategy: str,
    acceptance: str,
    previous: str | None = None,
    feedback: str | None = None,
) -> FunctionCandidate:
    """
    Raises GraniteError.

    *acceptance* holds what a developer would be handed: the failing test with
    its output and the checks that must keep passing. The sandbox still decides.
    """
    return await chat_json(
        f"You fix bugs in Python. Rewrite the function {function_name}() to fix the "
        f"diagnosed bug using this strategy: {strategy}. The fix must make the failing "
        "test pass and keep every existing check passing. The code runs offline: no "
        "network calls, no downloads, no raising where the original returned a value. "
        "Return the complete function, same name and signature, standard 4-space "
        "indentation, no surrounding code. Use only names already imported in the "
        "module or imports placed inside the function. Fields: approach (one sentence "
        "naming your strategy), function_source. " + _JSON_ONLY,
        f"Bug: {issue.title}\n{issue.description}\n\nDiagnosed root cause: {root_cause}\n\n"
        f"Current function:\n{current_source}\n\nAcceptance criteria:\n{acceptance}\n\n"
        f"Rest of the module and its settings:\n{file_context}"
        + (
            f"\n\nYour previous attempt:\n{previous}\n\nIt was tested in the sandbox and "
            f"failed:\n{feedback}\nRevise it so every check passes."
            if previous and feedback
            else ""
        ),
        FunctionCandidate,
        max_tokens=1500,
    )


async def diff_candidate(
    issue: Issue,
    root_cause: str,
    files: dict[str, str],
    strategy: str,
    *,
    tested: bool = False,
) -> DiffCandidate:
    """Raises GraniteError."""
    use = (
        "The patch will be applied with `git apply` from the repository root and "
        "tested, so file paths and context lines must match the files exactly."
        if tested
        else "The patch will be reviewed by a human, not executed."
    )
    return await chat_json(
        "You propose a fix as a unified diff (---/+++ headers, @@ hunks) against the "
        f"files shown, using this strategy: {strategy}. {use} Fields: approach (one "
        "sentence), patch (unified diff text), explanation (why it fixes the root "
        "cause). " + _JSON_ONLY,
        f"Issue: {issue.title}\n{issue.description}\n\nRoot cause: {root_cause}\n\n"
        f"Source code:\n{format_sources(files)}",
        DiffCandidate,
        max_tokens=1800,
    )
