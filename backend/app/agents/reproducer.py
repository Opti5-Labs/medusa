"""
Model-written reproducer tests for general repositories.

    draft = await write_reproducer(issue, files, root_cause, previous=..., feedback=...)

Asks Granite, or Bob when Granite is unavailable, for one pytest file that
fails on the current code because of the reported bug. The sandbox decides
whether it really does (app/pipelines/repro.py); nothing here trusts the model.
"""

import ast
import re
from dataclasses import dataclass

from pydantic import Field

from app.agents import bob, granite
from app.agents.granite import LenientModel
from app.agents.investigators import format_sources
from app.models.contracts import Issue

_MAX_TEST_CHARS = 12_000


class ReproTest(LenientModel):
    test_source: str
    explanation: str = Field(default="")


@dataclass
class Draft:
    source: str | None
    author: str | None  # "granite" | "bob"
    error: str | None = None


_SYSTEM = (
    "You write a single pytest test file that checks a reported bug in the "
    "repository shown. Assert the CORRECT behaviour the report describes, taken "
    "from the documentation, docstrings or the report itself: if the bug is real the "
    "test fails on the current code with an assertion failure (not an import or "
    "syntax error), and it passes once the bug is fixed. If the code is actually "
    "correct the test passes, which is a valid result; never weaken, invert or "
    "invent an expectation to make it fail. Import the project's modules the way "
    "its own tests do. Use only "
    "pytest and the standard library plus the project's own dependencies; no "
    "network, no files outside a tmp_path fixture, no sleeps. Keep it short: one or "
    "two test functions named test_*. Everything you need is in this message: "
    "answer directly, without opening or searching files. "
    'Reply with ONLY one JSON object: {"test_source": "<the complete test file>", '
    '"explanation": "<one sentence>"}'
)


_FENCE = re.compile(r"```(?:python|py)?[ \t]*\n(.*?)```", re.DOTALL)


def code_from(text: str) -> str:
    """The test file itself, when a model wrapped it in a Markdown code fence."""
    match = _FENCE.search(text)
    return match.group(1) if match else text


def validate(source: str) -> str | None:
    """None if *source* is a usable test file, else why not."""
    if not source.strip():
        return "the test file is empty"
    if len(source) > _MAX_TEST_CHARS:
        return "the test file is too long"
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return f"the test file is not valid Python ({exc.msg}, line {exc.lineno})"
    if not any(
        isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name.startswith("test")
        for node in ast.walk(tree)
    ):
        return "the file defines no test function"
    return None


def _user_prompt(
    issue: Issue,
    files: dict[str, str],
    root_cause: str | None,
    previous: str | None,
    feedback: str | None,
) -> str:
    where = issue.file or "unknown file"
    if issue.function:
        where += f" :: {issue.function}()"
    parts = [
        f"Bug report:\nTitle: {issue.title}\nLocation: {where}\nReport: {issue.description}",
    ]
    if root_cause:
        parts.append(f"Investigators' diagnosis:\n{root_cause}")
    parts.append(f"Source code:\n{format_sources(files)}")
    if previous and feedback:
        parts.append(
            f"Your previous test:\n{previous}\n\nIt was run in the sandbox and did not "
            f"reproduce the bug:\n{feedback}\nWrite a corrected test."
        )
    return "\n\n".join(parts)


async def write_reproducer(
    issue: Issue,
    files: dict[str, str],
    root_cause: str | None,
    *,
    previous: str | None = None,
    feedback: str | None = None,
) -> Draft:
    """One reproducer draft. Never raises; `error` explains an empty draft."""
    user = _user_prompt(issue, files, root_cause, previous, feedback)
    granite_reason: str | None = None
    if granite.is_configured():
        try:
            reply = await granite.chat_json(_SYSTEM, user, ReproTest, max_tokens=1800)
            source = code_from(reply.test_source)
            if (problem := validate(source)) is None:
                return Draft(source, "granite")
            granite_reason = f"Granite's test was unusable: {problem}"
        except granite.GraniteError as exc:
            granite_reason = str(exc)
    else:
        granite_reason = "Granite is not configured on this server"

    # The source is already in the prompt; an empty workspace keeps Bob from
    # spending its turn limit re-reading the same files.
    # text_field: code inside a JSON string is easy to mis-escape, so a damaged
    # wrapper still yields the test source.
    answer = await bob.ask(
        _SYSTEM + "\n\n" + user, {}, ReproTest, text_field="test_source"
    )
    if answer.status == "ok" and isinstance(answer.data, ReproTest):
        source = code_from(answer.data.test_source)
        if (problem := validate(source)) is None:
            return Draft(source, "bob")
        return Draft(
            None, None, f"{granite_reason}. Bob's test was unusable: {problem}"
        )
    return Draft(None, None, f"{granite_reason}. Bob: {answer.error or 'no answer'}")
