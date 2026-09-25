"""
Scan pipeline interface.

scan_tree(root, source) -> ScanResult

Walks *root*, applies ingest limits, detects dominant language, reads files,
and (when Granite is available) calls agents/granite.py for analysis.

Until Granite exists, returns an empty issue list with a clear warning.
Never fabricates issues.
"""

import logging
import os
import uuid
from collections import Counter
from pathlib import Path
from typing import Literal

from app.ingest.limits import filter_tree
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


def _detect_language(files: list[Path]) -> str:
    counts: Counter[str] = Counter()
    for f in files:
        lang = _LANG_MAP.get(f.suffix.lower())
        if lang:
            counts[lang] += 1
    if not counts:
        return "unknown"
    return counts.most_common(1)[0][0]


def scan_tree(
    root: Path,
    source: Literal["demo", "github", "zip"],
    extra_issues: list[Issue] | None = None,
) -> ScanResult:
    """
    Walk *root*, apply limits, return a ScanResult.

    *extra_issues* allows callers (e.g. github.py) to inject GitHub Issues
    that are merged into the final result.

    Granite analysis is not yet implemented; a warning is added to the result.
    """
    selected, warnings = filter_tree(root)

    rel_paths = [str(f.relative_to(root)) for f in selected]
    language = _detect_language(selected)

    # Count total files in the tree (for files_total)
    total = sum(len(files) for _, _, files in os.walk(root, followlinks=False))

    issues: list[Issue] = list(extra_issues or [])
    warnings.append(
        "Code analysis is not available yet — Granite integration is pending. "
        "Zero issues were found by automated analysis."
    )

    return ScanResult(
        scan_id=str(uuid.uuid4()),
        repo_source=source,
        language=language,
        files_scanned=rel_paths,
        files_total=total,
        issues=issues,
        warnings=warnings,
    )
