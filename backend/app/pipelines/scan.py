"""
Scan pipeline.

    result = await scan_repo(root, source, extra_issues, deadline)

Walks *root*, applies ingest limits, detects the dominant language, then asks
Granite to review the selected files in chunks (concurrently, max
GRANITE_MAX_CONCURRENT_CHUNKS in flight). If Granite cannot analyse any of it
(not configured, quota used up, not authorised), Bob reviews the same files in
one read-only run instead. Files are read as text only; each issue records
which agent found it.

A chunk that fails or returns invalid JSON is skipped with a warning; a bad
chunk never fails the scan. Chunks unfinished at the deadline are skipped the
same way, so a slow model returns partial results instead of a timeout.
Issues are never fabricated: without Granite the scan says so and lists none.
"""

import ast
import asyncio
import logging
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import Field

from app import config
from app.agents import bob, granite
from app.ingest.limits import filter_tree_with_total
from app.models.contracts import Issue, ScanResult

log = logging.getLogger(__name__)

# Extension -> language name
_LANG_MAP: dict[str, str] = {
    ".py": "Python",
    ".js": "JavaScript",
    ".ts": "TypeScript",
    ".jsx": "JavaScript",
    ".tsx": "TypeScript",
    ".java": "Java",
    ".go": "Go",
    ".rs": "Rust",
    ".c": "C",
    ".cpp": "C++",
    ".cc": "C++",
    ".cs": "C#",
    ".rb": "Ruby",
    ".php": "PHP",
    ".swift": "Swift",
    ".kt": "Kotlin",
    ".scala": "Scala",
    ".sh": "Shell",
}

_PRIORITY_ORDER = {"High": 0, "Medium": 1, "Low": 2}
_CATEGORIES = {"security", "correctness", "performance", "maintainability"}


def _detect_language(files: list[Path]) -> str:
    counts: Counter[str] = Counter()
    for f in files:
        lang = _LANG_MAP.get(f.suffix.lower())
        if lang:
            counts[lang] += 1
    if not counts:
        return "unknown"
    return counts.most_common(1)[0][0]


# ── Chunking ──────────────────────────────────────────────────────────────────


@dataclass
class Segment:
    path: str  # repo-relative
    start: int  # 1-based first line
    lines: list[str]


@dataclass
class Chunk:
    segments: list[Segment]

    @property
    def size(self) -> int:
        return sum(len(s.lines) for s in self.segments)


def _python_breaks(text: str) -> list[int]:
    """0-based line indexes where top-level definitions start."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    return sorted({(n.lineno - 1) for n in tree.body if hasattr(n, "lineno")})


def _generic_breaks(lines: list[str]) -> list[int]:
    """Lines that start a top-level block in brace or indentation languages."""
    return [
        i
        for i, line in enumerate(lines)
        if line
        and not line[0].isspace()
        and line.strip() not in ("}", "};", ")", "end")
    ]


def split_file(path: str, text: str, limit: int) -> list[Segment]:
    lines = text.splitlines()
    if len(lines) <= limit:
        return [Segment(path, 1, lines)]
    breaks = _python_breaks(text) if path.endswith(".py") else _generic_breaks(lines)
    segments: list[Segment] = []
    start = 0
    while start < len(lines):
        end = min(start + limit, len(lines))
        if end < len(lines):
            # Cut at the last definition boundary inside the window, if any.
            inside = [b for b in breaks if start < b < end]
            if inside:
                end = inside[-1]
        segments.append(Segment(path, start + 1, lines[start:end]))
        start = end
    return segments


def build_chunks(root: Path, files: list[Path], limit: int) -> list[Chunk]:
    """Split long files, then pack small segments together up to *limit* lines."""
    chunks: list[Chunk] = []
    current = Chunk([])
    budget = config.SCAN_MAX_LINES  # hard cap even if one file alone is larger
    for f in files:
        try:
            text = f.read_text("utf-8", errors="replace")
        except OSError:
            continue
        # .as_posix(), not str(): on Windows, Path.relative_to() renders with
        # backslashes, but this string is shown to the model in the ### FILE
        # header and matched back against its response in _to_issue() (and
        # stored as Issue.file). A backslash-vs-forward-slash mismatch made
        # every finding for any nested file (i.e. virtually all real repos)
        # silently unmatched and dropped — confirmed by a real test failure.
        for seg in split_file(f.relative_to(root).as_posix(), text, limit):
            if budget <= 0:
                break
            if len(seg.lines) > budget:
                seg.lines = seg.lines[:budget]
            budget -= len(seg.lines)
            if current.segments and current.size + len(seg.lines) > limit:
                chunks.append(current)
                current = Chunk([])
            current.segments.append(seg)
    if current.segments:
        chunks.append(current)
    return chunks


def _render(chunk: Chunk) -> str:
    parts = []
    for seg in chunk.segments:
        body = "\n".join(
            f"{seg.start + i:>5} | {line}" for i, line in enumerate(seg.lines)
        )
        parts.append(f"### FILE: {seg.path}\n{body}")
    return "\n\n".join(parts)


# ── Granite analysis ──────────────────────────────────────────────────────────


class _Finding(granite.LenientModel):
    file: str
    line: int | None = None
    function: str | None = None
    title: str
    description: str
    priority: str = "Medium"
    category: str | None = None


class _ChunkReview(granite.LenientModel):
    issues: list[_Finding] = Field(default_factory=list)


_SYSTEM = (
    "You are a meticulous code reviewer. Report only real defects you can point to in "
    "the code shown: bugs, security flaws, performance problems, or serious "
    "maintainability risks. Do not report style nits or speculation. If there are no "
    "real problems, return an empty list. Each issue: file (exactly as in the ### FILE "
    "header), line (the numbered line), function (or null), title (under 80 chars), "
    "description (what goes wrong and when, 1-3 sentences), priority (High, Medium or "
    "Low), category (security, correctness, performance or maintainability). "
    'Respond with a single JSON object only: {"issues": [...]}'
)


def _to_issue(f: _Finding, chunk: Chunk) -> Issue | None:
    seg = next((s for s in chunk.segments if s.path == f.file), None)
    if seg is None:  # model cited a file it was not shown
        return None
    line = f.line
    if line is not None and not (seg.start <= line < seg.start + len(seg.lines)):
        line = None
    priority = (
        f.priority.capitalize()
        if f.priority.capitalize() in _PRIORITY_ORDER
        else "Medium"
    )
    category = (
        f.category.lower() if f.category and f.category.lower() in _CATEGORIES else None
    )
    return Issue(
        id=str(uuid.uuid4()),
        title=f.title.strip()[:120],
        description=f.description.strip()[:1000],
        priority=priority,  # type: ignore[arg-type]
        source="scan",
        category=category,  # type: ignore[arg-type]
        file=seg.path,
        function=(f.function or None) and f.function.strip()[:120],
        line=line,
        found_by="granite",
    )


async def _review_chunk(chunk: Chunk) -> list[Issue]:
    review = await granite.chat_json(
        _SYSTEM, _render(chunk), _ChunkReview, max_tokens=1500
    )
    return [i for f in review.issues if (i := _to_issue(f, chunk)) is not None]


async def analyze(
    root: Path, files: list[Path], deadline: float
) -> tuple[list[Issue], list[str]]:
    """Review *files* with Granite until *deadline* (loop time). Returns (issues, warnings)."""
    issues, warnings, _ = await _analyze(root, files, deadline)
    return issues, warnings


async def _analyze(
    root: Path, files: list[Path], deadline: float
) -> tuple[list[Issue], list[str], str | None]:
    """As analyze(), plus the reason Granite is unavailable when no chunk succeeded."""
    chunks = await asyncio.to_thread(build_chunks, root, files, config.SCAN_CHUNK_LINES)
    if not chunks:
        return [], [], None
    tasks = [asyncio.create_task(_review_chunk(c)) for c in chunks]
    timeout = max(1.0, deadline - asyncio.get_running_loop().time())
    done, pending = await asyncio.wait(tasks, timeout=timeout)
    for t in pending:
        t.cancel()

    issues: list[Issue] = []
    failed = 0
    last_error = ""
    unavailable_error: str | None = None
    for t in done:
        exc = t.exception()
        if exc is None:
            issues.extend(t.result())
        else:
            failed += 1
            last_error = str(exc)
            if isinstance(exc, granite.GraniteError) and not exc.retryable:
                unavailable_error = last_error
    warnings: list[str] = []
    if failed:
        warnings.append(
            f"{failed} of {len(chunks)} code chunks could not be analysed and were skipped ({last_error})"
        )
    if pending:
        warnings.append(
            f"{len(pending)} of {len(chunks)} code chunks were skipped because the scan time limit was reached."
        )

    seen: set[tuple[str | None, str]] = set()
    unique: list[Issue] = []
    for issue in sorted(
        issues, key=lambda i: (_PRIORITY_ORDER[i.priority], i.file or "", i.line or 0)
    ):
        key = (issue.file, issue.title.lower())
        if key not in seen:
            seen.add(key)
            unique.append(issue)
    if len(unique) > config.SCAN_MAX_ISSUES:
        warnings.append(
            f"Showing the {config.SCAN_MAX_ISSUES} highest-priority of {len(unique)} issues found."
        )
        unique = unique[: config.SCAN_MAX_ISSUES]
    granite_down = unavailable_error if failed == len(chunks) else None
    return unique, warnings, granite_down


# ── Entry point ───────────────────────────────────────────────────────────────


# ── Bob scan (when Granite cannot analyse the code) ───────────────────────────

_BOB_SCAN_MAX_CHARS = (
    40_000  # code shown to Bob in one run, to stay inside BOB_MAX_COST
)
_BOB_SCAN_MAX_ISSUES = 10


class _BobScan(granite.LenientModel):
    issues: list[_Finding] = Field(default_factory=list)


def _bob_scan_files(root: Path, files: list[Path]) -> dict[str, str]:
    """The selected files (already in priority order) up to the character budget."""
    chosen: dict[str, str] = {}
    used = 0
    for f in files:
        try:
            text = f.read_text("utf-8", errors="replace")
        except OSError:
            continue
        if used + len(text) > _BOB_SCAN_MAX_CHARS:
            if chosen:
                continue
            text = text[:_BOB_SCAN_MAX_CHARS]
        chosen[f.relative_to(root).as_posix()] = text  # see build_chunks re: Windows
        used += len(text)
    return chosen


def _bob_scan_prompt(files: dict[str, str]) -> str:
    listing = "\n\n".join(
        f"### FILE: {path}\n"
        + "\n".join(f"{i:>5} | {line}" for i, line in enumerate(text.splitlines(), 1))
        for path, text in files.items()
    )
    return (
        "You are a meticulous code reviewer. The files below (also in your workspace) "
        "are read as text; nothing can be executed. Do not edit files or run commands. "
        "Report only real defects you can point to in this code: bugs, security flaws, "
        "performance problems or serious maintainability risks, at most "
        f"{_BOB_SCAN_MAX_ISSUES}, most important first. No style nits, no speculation; "
        "an empty list is fine. Reply with ONLY one JSON object: "
        '{"issues": [{"file": "exactly as in the ### FILE header", "line": number, '
        '"function": "name or null", "title": "under 80 chars", "description": '
        '"what goes wrong and when, 1-3 sentences", "priority": "High|Medium|Low", '
        '"category": "security|correctness|performance|maintainability"}]}\n\n'
        + listing
    )


def _bob_issue(f: _Finding, files: dict[str, str]) -> Issue | None:
    text = files.get(f.file)
    if text is None:  # Bob cited a file it was not shown
        return None
    n_lines = len(text.splitlines())
    line = f.line if f.line is not None and 1 <= f.line <= n_lines else None
    priority = (
        f.priority.capitalize()
        if f.priority.capitalize() in _PRIORITY_ORDER
        else "Medium"
    )
    category = (
        f.category.lower() if f.category and f.category.lower() in _CATEGORIES else None
    )
    return Issue(
        id=str(uuid.uuid4()),
        title=f.title.strip()[:120],
        description=f.description.strip()[:1000],
        priority=priority,  # type: ignore[arg-type]
        source="scan",
        category=category,  # type: ignore[arg-type]
        file=f.file,
        function=(f.function or None) and f.function.strip()[:120],
        line=line,
        found_by="bob",
    )


def _nobody_could(granite_reason: str, bob_reason: str) -> str:
    return (
        "Code analysis is unavailable, so no issues were generated by automated "
        f"analysis. Granite: {granite_reason.rstrip('.')}. Bob: {bob_reason}"
    )


async def bob_scan(
    root: Path, files: list[Path], deadline: float, granite_reason: str
) -> tuple[list[Issue], list[str]]:
    """Scan with Bob because Granite cannot. Returns (issues, warnings); never raises."""
    shown = await asyncio.to_thread(_bob_scan_files, root, files)
    if not shown:
        return [], []
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining < 20:
        return [], [
            _nobody_could(granite_reason, "not enough of the scan time limit was left")
        ]
    answer = await bob.ask(
        _bob_scan_prompt(shown),
        shown,
        _BobScan,
        timeout_s=min(remaining, config.BOB_TIMEOUT_S),
    )
    if answer.status != "ok" or not isinstance(answer.data, _BobScan):
        return [], [_nobody_could(granite_reason, answer.error or "unknown error")]
    issues = [i for f in answer.data.issues if (i := _bob_issue(f, shown)) is not None]
    issues.sort(key=lambda i: (_PRIORITY_ORDER[i.priority], i.file or "", i.line or 0))
    skipped = len(files) - len(shown)
    cost = f" ({answer.cost} Bobcoins)" if answer.cost is not None else ""
    note = (
        f"Code analysed by IBM Bob because Granite is unavailable ({granite_reason}). "
        f"Bob reviewed {len(shown)} of the {len(files)} selected files{cost}."
    )
    if skipped:
        note += f" {skipped} file(s) were left out to stay within the Bob cost limit."
    return issues[:_BOB_SCAN_MAX_ISSUES], [note]


async def scan_repo(
    root: Path,
    source: Literal["demo", "github", "zip"],
    extra_issues: list[Issue] | None = None,
    deadline: float | None = None,
) -> ScanResult:
    """Select files, analyse them with Granite (or Bob if Granite is unavailable)."""
    selected, warnings, total = await asyncio.to_thread(filter_tree_with_total, root)
    rel_paths = [f.relative_to(root).as_posix() for f in selected]  # see build_chunks

    scan_issues: list[Issue] = []
    if not selected:
        warnings.append("No source files were found to analyse.")
    else:
        if deadline is None:
            deadline = asyncio.get_running_loop().time() + config.SCAN_TIMEOUT_S - 5
        granite_down: str | None
        if granite.is_configured():
            scan_issues, analysis_warnings, granite_down = await _analyze(
                root, selected, deadline
            )
            warnings.extend(analysis_warnings)
        else:
            granite_down = "Granite is not configured on this server"
        if granite_down is not None:
            # Granite could not look at the code at all: Bob scans instead.
            scan_issues, bob_warnings = await bob_scan(
                root, selected, deadline, granite_down
            )
            warnings.extend(bob_warnings)

    return ScanResult(
        scan_id=str(uuid.uuid4()),
        repo_source=source,
        language=_detect_language(selected),
        files_scanned=rel_paths,
        files_total=total,
        issues=[*scan_issues, *(extra_issues or [])],
        warnings=warnings,
    )
