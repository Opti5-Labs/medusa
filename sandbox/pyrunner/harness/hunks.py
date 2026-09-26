"""
A forgiving unified-diff applier, used only when strict `git apply` refuses a
patch. Runs inside the sandbox.

Model-written diffs often have wrong @@ line counts, stale line numbers, or no
trailing context (which git anchors to the end of the file). Here each hunk is
located by its old lines (context plus removed) and replaced by its new lines.
The old lines must match exactly (trailing whitespace aside); the @@ start is
only a hint for choosing between several matches.

    apply(repo_dir, diff_text)   # raises PatchError with a readable reason
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

_HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@")


class PatchError(Exception):
    pass


@dataclass
class _Hunk:
    start: int  # 1-based hint from the @@ header
    old: list[str] = field(default_factory=list)
    new: list[str] = field(default_factory=list)


@dataclass
class _FilePatch:
    old_path: str | None  # None for a new file
    new_path: str | None  # None for a deleted file
    hunks: list[_Hunk] = field(default_factory=list)


def _path(header: str) -> str | None:
    name = header[4:].split("\t")[0].strip()
    if name == "/dev/null":
        return None
    if name[:2] in ("a/", "b/"):
        name = name[2:]
    return name


def parse(text: str) -> list[_FilePatch]:
    files: list[_FilePatch] = []
    hunk: _Hunk | None = None
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            files.append(_FilePatch(_path(line), _path(lines[i + 1])))
            hunk = None
            i += 2
            continue
        m = _HUNK.match(line)
        if m and files:
            hunk = _Hunk(int(m.group(1)))
            files[-1].hunks.append(hunk)
        elif hunk is not None:
            if line.startswith("\\"):  # "\ No newline at end of file"
                pass
            elif line.startswith("-"):
                hunk.old.append(line[1:])
            elif line.startswith("+"):
                hunk.new.append(line[1:])
            elif line.startswith(" ") or line == "":
                hunk.old.append(line[1:])
                hunk.new.append(line[1:])
            else:  # anything else ends the hunk (e.g. a "diff --git" line)
                hunk = None
        i += 1
    if not files or not any(f.hunks or f.old_path is None for f in files):
        raise PatchError("no file changes found in the patch")
    return files


def _find(lines: list[str], old: list[str], hint: int, start: int) -> int | None:
    """Index where *old* occurs at or after *start*, nearest to *hint*."""
    if not old:
        return min(max(hint - 1, start), len(lines))
    for norm in (lambda s: s, str.rstrip):
        target = [norm(s) for s in old]
        hits = [
            i
            for i in range(start, len(lines) - len(old) + 1)
            if [norm(s) for s in lines[i : i + len(old)]] == target
        ]
        if hits:
            return min(hits, key=lambda i: abs(i - (hint - 1)))
    return None


def _safe(repo: Path, rel: str) -> Path:
    path = (repo / rel).resolve()
    if not path.is_relative_to(repo.resolve()):
        raise PatchError(f"{rel}: outside the repository")
    return path


def apply(repo: Path, text: str) -> None:
    """Apply every file's hunks, or change nothing and raise PatchError."""
    results: dict[Path, str | None] = {}
    for fp in parse(text):
        if fp.old_path is None:  # new file
            target = _safe(repo, fp.new_path or "")
            if target.exists():
                raise PatchError(f"{fp.new_path}: already exists")
            results[target] = "\n".join(h for hk in fp.hunks for h in hk.new) + "\n"
            continue
        source = _safe(repo, fp.old_path)
        if not source.is_file():
            raise PatchError(f"{fp.old_path}: no such file")
        content = source.read_text()
        lines = content.splitlines()
        pos = 0
        for n, hunk in enumerate(fp.hunks, 1):
            at = _find(lines, hunk.old, hunk.start, pos)
            if at is None:
                raise PatchError(
                    f"{fp.old_path}: hunk {n} does not match the file "
                    f"(looked for the lines around line {hunk.start})"
                )
            lines[at : at + len(hunk.old)] = hunk.new
            pos = at + len(hunk.new)
        if fp.new_path is None:
            results[source] = None
        else:
            ending = "\n" if content.endswith("\n") or not content else ""
            results[source] = "\n".join(lines) + ending
            if fp.new_path != fp.old_path:
                results[_safe(repo, fp.new_path)] = results.pop(source)
                results[source] = None
    for path, new_text in results.items():
        if new_text is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(new_text)
