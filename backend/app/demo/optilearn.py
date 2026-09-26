"""
The OptiLearn demo scenario: a real bug in OptiLearn's Whisper fallback.

The bundled source (sandbox/optilearn/src) is a subset of OptiLearn at the
commit that reverted the fix. Candidates are full replacements for the target
function; this module splices them into a fresh copy of the source, which the
sandbox then mounts read-only.
"""

import ast
import difflib
import io
import json
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

from app.config import OPTILEARN_DIR, OPTILEARN_PREPARED, OPTILEARN_SRC
from app.models.contracts import PatchStats

SCENARIO_ID = "whisper_hf_local_path"
TARGET_FILE = "app/services/whisper_client.py"
TARGET_FUNCTION = "_resolve_hf_asr_model"


class CandidateRejected(ValueError):
    """A candidate's source cannot be applied. The message is shown in its panel."""


@dataclass
class PreparedCandidate:
    approach: str
    source: str


def read_target() -> str:
    return (OPTILEARN_SRC / TARGET_FILE).read_text(encoding="utf-8")


def _function_span(tree: ast.Module, name: str) -> tuple[int, int]:
    for node in tree.body:
        if (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name == name
        ):
            start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
            return start, node.end_lineno or node.lineno
    raise CandidateRejected(f"{name}() not found")


def target_function_source() -> str:
    text = read_target()
    start, end = _function_span(ast.parse(text), TARGET_FUNCTION)
    return "\n".join(text.splitlines()[start - 1 : end]) + "\n"


def splice(new_function_source: str) -> str:
    """Return the target file with the function replaced. Raises CandidateRejected."""
    source = new_function_source.strip("\n") + "\n"
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise CandidateRejected(
            f"patch is not valid Python: {exc.msg} (line {exc.lineno})"
        ) from exc
    if (
        len(tree.body) != 1
        or not isinstance(tree.body[0], ast.FunctionDef)
        or tree.body[0].name != TARGET_FUNCTION
    ):
        raise CandidateRejected(
            f"patch must be exactly one definition of {TARGET_FUNCTION}()"
        )

    original = read_target()
    start, end = _function_span(ast.parse(original), TARGET_FUNCTION)
    lines = original.splitlines(keepends=True)
    patched = "".join(lines[: start - 1]) + source + "".join(lines[end:])
    ast.parse(patched)  # the spliced file must still parse
    if _normalise(patched) == _normalise(original):
        raise CandidateRejected("the function is unchanged")
    return patched


def _normalise(text: str) -> str:
    """Compare code ignoring formatting and comments."""
    return ast.dump(ast.parse(text))


def unified_diff(new_text: str) -> str:
    return "".join(
        difflib.unified_diff(
            read_target().splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=f"a/optilearn/{TARGET_FILE}",
            tofile=f"b/optilearn/{TARGET_FILE}",
        )
    )


def patch_stats(diff: str) -> PatchStats:
    added = removed = files = 0
    for line in diff.splitlines():
        if line.startswith("+++ "):
            files += 1
            continue
        if line.startswith("--- "):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return PatchStats(files_changed=files, lines_added=added, lines_removed=removed)


def make_workdir(parent: Path, name: str, target_text: str | None = None) -> Path:
    """Copy the bundled source to parent/name, optionally replacing the target file."""
    dest = parent / name
    shutil.copytree(OPTILEARN_SRC, dest)
    if target_text is not None:
        (dest / TARGET_FILE).write_text(target_text, encoding="utf-8")
    return dest


def load_prepared() -> list[PreparedCandidate]:
    manifest = json.loads((OPTILEARN_PREPARED / "candidates.json").read_text("utf-8"))
    return [
        PreparedCandidate(
            approach=item["approach"],
            source=(OPTILEARN_PREPARED / item["file"]).read_text("utf-8"),
        )
        for item in manifest["candidates"]
    ]


def reproducer_source() -> str:
    return (OPTILEARN_DIR / "harness" / "tests" / "test_reproducer.py").read_text(
        "utf-8"
    )


def acceptance_criteria() -> str:
    """The reproducer and behaviour checks a fix must satisfy, as given to fixers."""
    tests = OPTILEARN_DIR / "harness" / "tests"
    return (
        "Failing test (must pass after the fix):\n"
        + (tests / "test_reproducer.py").read_text("utf-8")
        + "\n\nExisting behaviour (must keep passing):\n"
        + (tests / "test_behaviour.py").read_text("utf-8")
    )


def _module_through_target() -> str:
    """The target module from line 1 to the end of the target function."""
    text = read_target()
    _, end = _function_span(ast.parse(text), TARGET_FUNCTION)
    return "\n".join(text.splitlines()[:end]) + "\n"


def _settings_excerpt() -> tuple[int, str]:
    """(first line number, text) of the Whisper settings in config.py."""
    lines = (OPTILEARN_SRC / "app/core/config.py").read_text("utf-8").splitlines()
    hits = [i for i, line in enumerate(lines) if "WHISPER" in line]
    start, end = hits[0], hits[-1]
    return start + 1, "\n".join(lines[start : end + 1]) + "\n"


def fixer_context() -> str:
    """The module up to the target function, plus the settings it reads."""
    first, settings = _settings_excerpt()
    return (
        _module_through_target()
        + f"\n# ---- app/core/config.py, Settings excerpt from line {first} ----\n"
        + settings
    )


def context_files() -> dict[str, str]:
    """
    Source shown to investigators, keyed by repo-relative path. Only the code
    the bug lives in, to keep model calls small; line numbers match the files.
    """
    first, settings = _settings_excerpt()
    return {
        f"optilearn/{TARGET_FILE}": _module_through_target(),
        f"optilearn/app/core/config.py#L{first}": settings,
    }


def zip_tree(root: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(root.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                zf.write(path, Path("optilearn") / path.relative_to(root))
    return buffer.getvalue()
