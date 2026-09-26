"""
Retrieval, redaction and instant answers for "Ask Medusa".

Decides what to read from a scanned repo and whether a question needs a model
at all. Files are read as text only; nothing here executes repo content or calls
a model.
"""

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app import config
from app.ingest.limits import (
    _CODE_SUFFIXES,
    _CONFIG_DOC_SUFFIXES,
    SKIP_DIRS,
    SKIP_SUFFIXES,
    _is_minified,
    is_binary,
)
from app.models.contracts import AskAnswer, AskCitation, Issue
from app.pipelines.context import safe_path
from app.store import ScanRecord

log = logging.getLogger(__name__)

_MAX_READ_BYTES = 60_000
_DEMO_PREFIX = "optilearn/"
_REDACTED = "[REDACTED]"

STOPWORDS = frozenset(
    {
        "what",
        "why",
        "how",
        "does",
        "did",
        "the",
        "this",
        "that",
        "with",
        "from",
        "into",
        "are",
        "and",
        "for",
        "can",
        "please",
        "tell",
        "about",
        "explain",
        "issue",
        "issues",
        "bug",
        "bugs",
        "error",
        "repo",
        "repository",
        "code",
        "file",
        "files",
        "fix",
        "solve",
        "cause",
        "occur",
        "happen",
        "work",
        "works",
    }
)
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_CAMEL_RE = re.compile(r"[a-z][A-Z]")
_LONG_WORD = 8
_FALLBACK_STEMS = frozenset(
    {"main", "app", "index", "server", "cli", "manage", "__main__"}
)
_FALLBACK_MAX = 3
_WHOLE_FILE_LINES = 300
_WINDOW_LINES = 30
_MAX_WINDOWS = 4
_HEAD_LINES = 120


class AskUnavailable(Exception):
    """Raised with a message that is safe to show users."""


# ── Repo access ───────────────────────────────────────────────────────────────


def repo_root(record: ScanRecord) -> tuple[Path, str]:
    if record.root is None and record.result.repo_source == "demo":
        root, prefix = config.OPTILEARN_SRC, _DEMO_PREFIX
    else:
        root, prefix = record.root, ""
    if root is None or not root.is_dir():
        raise AskUnavailable(
            "The files for this scan are no longer available. "
            "Please run the scan again."
        )
    return root, prefix


def _tier(display_path: str) -> int:
    suffix = Path(display_path).suffix.lower()
    if suffix in _CODE_SUFFIXES:
        return 0
    return 1 if suffix in _CONFIG_DOC_SUFFIXES else 2


def list_repo_files(root: Path, prefix: str) -> list[str]:
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [
            d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")
        ]
        for name in filenames:
            path = Path(dirpath) / name
            if path.suffix.lower() in SKIP_SUFFIXES:
                continue
            if is_binary(path) or _is_minified(path):
                continue
            found.append(prefix + path.relative_to(root).as_posix())
    found.sort(key=lambda p: (_tier(p), p))
    return found[: config.ASK_MAX_INDEX_FILES]


def read_file(root: Path, prefix: str, display_path: str) -> str | None:
    rel = display_path
    if prefix:
        if not rel.startswith(prefix):
            return None
        rel = rel[len(prefix) :]
    path = safe_path(root, rel)
    if path is None:
        return None
    try:
        with open(path, "rb") as fh:
            return fh.read(_MAX_READ_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return None


# ── Redaction ─────────────────────────────────────────────────────────────────

_SECRET_RES = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL,
    ),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
]
_ASSIGN_RE = re.compile(
    r"""(?P<head>[\w.-]*(?:api_key|apikey|secret|token|passwd|password)[\w.-]*"""
    r"""["']?\s*[:=]\s*)(?P<q>["'])(?P<val>(?:(?!(?P=q)).)+)(?P=q)""",
    re.IGNORECASE,
)


def redact(text: str) -> str:
    for pattern in _SECRET_RES:
        text = pattern.sub(_REDACTED, text)
    return _ASSIGN_RE.sub(lambda m: f"{m['head']}{m['q']}{_REDACTED}{m['q']}", text)


# ── Retrieval ─────────────────────────────────────────────────────────────────


def question_terms(text: str) -> tuple[set[str], set[str]]:
    plain: set[str] = set()
    symbols: set[str] = set()
    for word in _WORD_RE.findall(text):
        lowered = word.lower()
        if lowered in STOPWORDS:
            continue
        plain.add(lowered)
        if "_" in word or _CAMEL_RE.search(word) or len(word) >= _LONG_WORD:
            symbols.add(lowered)
    return plain, symbols


def score_file(
    display_path: str,
    text_lower: str,
    question_lower: str,
    plain: set[str],
    symbols: set[str],
    scoped_file: str | None,
) -> int:
    path_lower = display_path.lower()
    score = 0
    if path_lower in question_lower or path_lower.rsplit("/", 1)[-1] in question_lower:
        score += 100
    if scoped_file is not None and display_path == scoped_file:
        score += 60
    score += 5 * min(3, sum(1 for t in plain if t in path_lower))
    score += 8 * min(5, sum(1 for t in symbols if t in text_lower))
    score += min(10, sum(1 for t in plain if t in text_lower))
    return score


@dataclass
class Picked:
    path: str
    text: str
    score: int


def _fallback_paths(all_paths: list[str]) -> list[str]:
    def key(p: str) -> tuple[int, str]:
        return (p.count("/"), p)

    def base(p: str) -> str:
        return p.rsplit("/", 1)[-1].lower()

    readmes = sorted((p for p in all_paths if base(p).startswith("readme")), key=key)
    entry = sorted(
        (p for p in all_paths if base(p).split(".", 1)[0] in _FALLBACK_STEMS),
        key=key,
    )
    return [*readmes, *entry][:_FALLBACK_MAX]


def pick_files(
    root: Path,
    prefix: str,
    all_paths: list[str],
    question: str,
    issue: Issue | None,
) -> list[Picked]:
    source = question
    if issue is not None:
        source = f"{question} {issue.title} {issue.description} {issue.function or ''}"
    plain, symbols = question_terms(source)
    scoped = issue.file if issue is not None else None
    question_lower = question.lower()

    scored: list[Picked] = []
    bytes_read = 0
    for path in all_paths:
        if bytes_read >= config.ASK_MAX_SEARCH_BYTES:
            break
        text = read_file(root, prefix, path)
        if text is None:
            continue
        bytes_read += len(text.encode("utf-8"))
        score = score_file(path, text.lower(), question_lower, plain, symbols, scoped)
        if score > 0:
            scored.append(Picked(path, text, score))

    if scored:
        scored.sort(key=lambda p: (-p.score, len(p.text), p.path))
        return scored[: config.ASK_MAX_FILES]

    fallback: list[Picked] = []
    for path in _fallback_paths(all_paths):
        text = read_file(root, prefix, path)
        if text is not None:
            fallback.append(Picked(path, text, 0))
    return fallback


@dataclass
class Excerpt:
    path: str
    start: int  # 1-based line number of lines[0]
    lines: list[str]


def make_excerpts(picked: Picked, terms: set[str]) -> list[Excerpt]:
    lines = picked.text.splitlines()
    if len(lines) <= _WHOLE_FILE_LINES:
        return [Excerpt(picked.path, 1, lines)]

    lowered = [line.lower() for line in lines]
    hits = [i for i, line in enumerate(lowered) if any(t in line for t in terms)]
    hits = hits[:_MAX_WINDOWS]
    if not hits:
        return [Excerpt(picked.path, 1, lines[:_HEAD_LINES])]

    spans: list[list[int]] = []
    for i in hits:
        lo, hi = max(0, i - _WINDOW_LINES), min(len(lines) - 1, i + _WINDOW_LINES)
        if spans and lo <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], hi)
        else:
            spans.append([lo, hi])
    return [Excerpt(picked.path, lo + 1, lines[lo : hi + 1]) for lo, hi in spans]


def _size(excerpt: Excerpt) -> int:
    return sum(len(line) + 1 for line in excerpt.lines)


def pack_excerpts(
    picked: list[Picked], terms: set[str], budget: int
) -> tuple[list[Excerpt], bool]:
    packed: list[Excerpt] = []
    total = 0
    for item in picked:
        for excerpt in make_excerpts(item, terms):
            size = _size(excerpt)
            if total + size <= budget:
                packed.append(excerpt)
                total += size
                continue
            if not packed:
                # a lone oversized excerpt is cut to fit rather than sending nothing
                cut: list[str] = []
                used = 0
                for line in excerpt.lines:
                    if used + len(line) + 1 > budget:
                        break
                    cut.append(line)
                    used += len(line) + 1
                if cut:
                    packed.append(Excerpt(excerpt.path, excerpt.start, cut))
            return packed, True
    return packed, False


def repo_map(paths: list[str]) -> str:
    shown = paths[: config.ASK_MAX_REPO_MAP_FILES]
    header = f"Repository map ({len(paths)} files, showing first {len(shown)}):"
    return "\n".join([header, *shown])


# ── Instant path ──────────────────────────────────────────────────────────────

_NOUNS = r"(issues|problems|bugs|findings)"
_INSTANT_RES = [
    re.compile(p)
    for p in (
        (
            r"^(what|which) (are )?(the )?(issues|problems|bugs|defects|findings)"
            r"( (are )?(there|found|detected))?"
            r"( (in|of) (this|the) (repo|repository|code|project|codebase|scan))?$"
        ),
        rf"^(list|show|give me|display) (me )?(all )?(the )?{_NOUNS}( found)?$",
        r"^(any|are there( any)?) (issues|problems|bugs)$",
        r"^(how many|number of) (issues|problems|bugs)( (are )?(there|found))?$",
        r"^(issues|problems|bugs)$",
    )
]
_MODEL_WORDS = frozenset(
    {
        "how",
        "why",
        "fix",
        "solve",
        "cause",
        "reason",
        "occur",
        "explain",
        "because",
        "resolve",
        "patch",
    }
)
_PUNCT = str.maketrans("", "", "?!.,;:")


def classify(question: str, issue_id: str | None) -> Literal["issue_list", "model"]:
    if issue_id is not None:
        return "model"
    normalised = " ".join(question.lower().translate(_PUNCT).split())
    # "how many" is a count request, not a "how do I" question
    if _MODEL_WORDS & set(normalised.replace("how many", "many").split()):
        return "model"
    if any(p.match(normalised) for p in _INSTANT_RES):
        return "issue_list"
    return "model"


_PRIORITY_ORDER = {"High": 0, "Medium": 1, "Low": 2}


def _issue_lines(index: int, issue: Issue) -> str:
    head = f"{index}. **{issue.priority}**: {issue.title}"
    if issue.file:
        where = f"{issue.file}:{issue.line}" if issue.line else issue.file
        head += f", `{where}`"
    parts = [
        issue.category,
        f"found by {issue.found_by.title()}" if issue.found_by else None,
    ]
    detail = " | ".join(p for p in parts if p)
    return f"{head}\n   {detail}" if detail else head


def issue_list_answer(record: ScanRecord, ask_id: str, question: str) -> AskAnswer:
    result = record.result
    scanned = len(result.files_scanned)
    issues = sorted(result.issues, key=lambda i: _PRIORITY_ORDER[i.priority])

    if not issues:
        body = [
            (
                f"No issues were found in the {scanned} files analysed. That does "
                f"not prove the rest of the code is bug-free: the scan analysed "
                f"{scanned} of {result.files_total} eligible files."
            ),
            *result.warnings[:2],
        ]
        answer = "\n\n".join(body)
    else:
        noun = "issue" if len(issues) == 1 else "issues"
        header = (
            f"Found **{len(issues)} {noun}** in this scan ({result.language}; "
            f"{scanned} of {result.files_total} eligible files analysed)."
        )
        listing = "\n".join(_issue_lines(n, i) for n, i in enumerate(issues, 1))
        footer = (
            "Ask me why any of these happens, or open an issue to reproduce "
            "and debug it."
        )
        answer = f"{header}\n\n{listing}\n\n{footer}"

    citations: list[AskCitation] = []
    for issue in issues:
        if issue.file:
            citation = AskCitation(file=issue.file, line=issue.line)
            if citation not in citations:
                citations.append(citation)

    return AskAnswer(
        ask_id=ask_id,
        question=question,
        answer=answer,
        grounding="scan_data",
        answered_by="scan",
        citations=citations,
        files_read=[],
    )
