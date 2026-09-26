"""
Pick the source files a general-repo reasoning call gets to read.

Files are read as text from the scan's extracted tree. Paths that come from a
model or an issue are resolved and must stay inside the tree.
"""

import re
from pathlib import Path

from app.models.contracts import Issue

_MAX_FILES = 4
_MAX_FILE_BYTES = 60_000
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")
_STOPWORDS = {
    "when",
    "with",
    "that",
    "this",
    "from",
    "into",
    "does",
    "should",
    "error",
    "issue",
    "bug",
}


def safe_path(root: Path, rel: str) -> Path | None:
    """Resolve *rel* under *root*, or None if it escapes or does not exist."""
    try:
        candidate = (root / rel.lstrip("/")).resolve()
        root_resolved = root.resolve()
    except (OSError, ValueError):
        return None
    if not candidate.is_relative_to(root_resolved) or not candidate.is_file():
        return None
    return candidate


def _read(path: Path) -> str | None:
    try:
        if path.stat().st_size > _MAX_FILE_BYTES:
            return path.read_bytes()[:_MAX_FILE_BYTES].decode("utf-8", "replace")
        return path.read_text("utf-8", errors="replace")
    except OSError:
        return None


def select_files(root: Path, scanned: list[str], issue: Issue) -> dict[str, str]:
    """The issue's own file first, then scanned files mentioning the issue's keywords."""
    chosen: dict[str, str] = {}
    if issue.file:
        path = safe_path(root, issue.file)
        if path and (text := _read(path)) is not None:
            chosen[issue.file] = text

    words = {
        w.lower()
        for w in _WORD_RE.findall(
            f"{issue.title} {issue.description} {issue.function or ''}"
        )
        if w.lower() not in _STOPWORDS
    }
    scored: list[tuple[int, str, str]] = []
    for rel in scanned:
        if rel in chosen:
            continue
        path = safe_path(root, rel)
        if path is None or (text := _read(path)) is None:
            continue
        lowered = text.lower()
        score = sum(1 for w in words if w in lowered) + sum(
            3 for w in words if w in rel.lower()
        )
        if score:
            scored.append((score, rel, text))
    scored.sort(key=lambda t: (-t[0], t[1]))
    for _, rel, text in scored[: _MAX_FILES - len(chosen)]:
        chosen[rel] = text
    return chosen


def select_files_for(
    root: Path | None, scanned: list[str], issue: Issue, scenario: str | None
) -> dict[str, str]:
    """
    Files for a reasoning-mode investigation. The demo's own issues (other
    than the sandboxed one) carry pre-bundled real source keyed by scenario,
    since there is no extracted tree to read from for them; every other
    reasoning-mode issue (github/zip) uses select_files() as usual.
    """
    if scenario:
        from app.demo.loader import load_issue_context

        bundled = load_issue_context(scenario)
        if bundled:
            return bundled
    return select_files(root, scanned, issue) if root else {}
