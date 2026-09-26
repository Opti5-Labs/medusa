"""
Ingest limits: file filtering and scan-cap logic.

All constants come from app/config.py — never hard-code limits here.

Public API:
    filter_tree(root: Path) -> tuple[list[Path], list[str]]
        Walk root, skip junk, apply scan cap, return (selected_files, warnings).
"""

import os
from pathlib import Path

from app.config import SCAN_MAX_FILES, SCAN_MAX_LINES

# ── Skip rules ────────────────────────────────────────────────────────────────

SKIP_DIRS: frozenset[str] = frozenset(
    {
        "node_modules",
        "dist",
        "build",
        ".git",
        "venv",
        ".venv",
        "__pycache__",
        "vendor",
        "third_party",
        ".next",
        "out",
        "coverage",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
    }
)

SKIP_SUFFIXES: frozenset[str] = frozenset(
    {
        # lock files
        ".lock",
        # compiled / binary
        ".pyc",
        ".pyo",
        ".so",
        ".dylib",
        ".dll",
        ".exe",
        ".o",
        ".a",
        ".class",
        ".jar",
        ".war",
        ".whl",
        ".egg",
        # images
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".ico",
        ".svg",
        ".webp",
        ".bmp",
        ".tiff",
        # fonts
        ".ttf",
        ".otf",
        ".woff",
        ".woff2",
        ".eot",
        # archives
        ".zip",
        ".tar",
        ".gz",
        ".bz2",
        ".xz",
        ".rar",
        ".7z",
        # media
        ".mp3",
        ".mp4",
        ".wav",
        ".avi",
        ".mov",
        ".mkv",
        # data blobs
        ".db",
        ".sqlite",
        ".sqlite3",
        ".bin",
        ".dat",
        # docs (binary)
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
        # map/min
        ".map",
    }
)

# Minified-file heuristics: these suffixes are always skipped
_MINIFIED_SUFFIXES: frozenset[str] = frozenset({".min.js", ".min.css"})

# Priority tier 1 — real source code (chosen first, consume budget first)
_CODE_SUFFIXES: frozenset[str] = frozenset(
    {
        ".py",
        ".js",
        ".ts",
        ".jsx",
        ".tsx",
        ".java",
        ".go",
        ".rs",
        ".c",
        ".cpp",
        ".cc",
        ".h",
        ".hpp",
        ".cs",
        ".rb",
        ".php",
        ".swift",
        ".kt",
        ".kts",
        ".scala",
        ".sh",
        ".bash",
        ".zsh",
    }
)

# Priority tier 2 — config/docs (fill leftover budget after real code)
_CONFIG_DOC_SUFFIXES: frozenset[str] = frozenset(
    {
        ".yaml",
        ".yml",
        ".toml",
        ".json",
        ".xml",
        ".html",
        ".css",
        ".scss",
        ".sass",
        ".md",
        ".txt",
        ".env",
        ".cfg",
        ".ini",
        ".conf",
    }
)

# Union of all recognised source-like extensions (used for completeness checks)
_SOURCE_SUFFIXES: frozenset[str] = _CODE_SUFFIXES | _CONFIG_DOC_SUFFIXES

_MAX_LINE_LENGTH = 500  # lines longer than this suggest minified content
_NUL_SNIFF_BYTES = 8_192  # bytes read to detect binary


def is_binary(path: Path) -> bool:
    """Return True if the file appears to be binary (contains NUL bytes)."""
    try:
        with open(path, "rb") as fh:
            chunk = fh.read(_NUL_SNIFF_BYTES)
        return b"\x00" in chunk
    except OSError:
        return True


def _is_minified(path: Path) -> bool:
    """Return True if the file looks like minified JS/CSS."""
    name = path.name.lower()
    if any(name.endswith(s) for s in _MINIFIED_SUFFIXES):
        return True
    # Heuristic: any single line > 500 chars in first 5 lines
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= 5:
                    break
                if len(line) > _MAX_LINE_LENGTH:
                    return True
    except OSError:
        pass
    return False


def _count_lines(path: Path) -> int:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def filter_tree(root: Path) -> tuple[list[Path], list[str]]:
    """Walk *root*, skip non-source files, apply scan cap. See filter_tree_with_total."""
    selected, warnings, _ = filter_tree_with_total(root)
    return selected, warnings


def filter_tree_with_total(root: Path) -> tuple[list[Path], list[str], int]:
    """
    Walk *root*, skip non-source files, apply scan cap.

    Priority order:
      1. Real source code (.py, .js, .ts, .go, etc.)
      2. Config / docs (.md, .json, .yaml, .html, etc.)
      3. Files with unknown-but-text extensions

    Within each tier files are sorted by path for determinism.

    The line budget is applied greedily: files that would individually push the
    total over SCAN_MAX_LINES are *skipped* (not everything after them dropped),
    so smaller files later in the list still get a chance.

    Returns:
        selected:     list of Path objects to scan (relative paths under root)
        warnings:     human-readable notices about selection / capping
        files_total:  eligible (non-junk, non-binary) files found
    """
    warnings: list[str] = []
    candidates: list[Path] = []

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        # Prune SKIP_DIRS in-place so os.walk won't descend into them
        dirnames[:] = [
            d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")
        ]

        for fname in filenames:
            fpath = Path(dirpath) / fname
            suffix = fpath.suffix.lower()

            if suffix in SKIP_SUFFIXES:
                continue
            if is_binary(fpath):
                continue
            if _is_minified(fpath):
                continue

            candidates.append(fpath)

    files_total = len(candidates)  # all eligible (non-junk, non-binary) files

    # Sort: code first (tier 0), config/doc second (tier 1), unknown last (tier 2),
    # then by path for determinism within each tier.
    def _sort_key(p: Path) -> tuple[int, str]:
        s = p.suffix.lower()
        if s in _CODE_SUFFIXES:
            return (0, str(p))
        if s in _CONFIG_DOC_SUFFIXES:
            return (1, str(p))
        return (2, str(p))

    candidates.sort(key=_sort_key)

    # Apply file cap before line budget
    file_capped = candidates[:SCAN_MAX_FILES]

    # Apply line budget — skip oversized files but keep trying smaller ones
    line_total = 0
    selected: list[Path] = []
    budget_hit = False

    for fpath in file_capped:
        n = _count_lines(fpath)
        if line_total + n > SCAN_MAX_LINES:
            budget_hit = True
            continue  # skip this file, try the next one
        line_total += n
        selected.append(fpath)

    # Edge case: budget already zero and first file alone exceeds it — include it
    # so there is always something to return when eligible files exist.
    if not selected and file_capped:
        selected = [file_capped[0]]
        budget_hit = False  # no useful warning; we included what we could

    # Emit a single, clear selection notice
    if budget_hit or len(candidates) > SCAN_MAX_FILES:
        warnings.append(
            f"Selected {len(selected)} of {files_total} eligible files "
            f"(line budget of {SCAN_MAX_LINES:,} reached)."
            if budget_hit
            else f"Selected {len(selected)} of {files_total} eligible files "
            f"(file cap of {SCAN_MAX_FILES} reached)."
        )

    return selected, warnings, files_total
