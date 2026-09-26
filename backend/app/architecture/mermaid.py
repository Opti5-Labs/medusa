"""
Deterministic, sanitized Mermaid generation from validated architecture JSON.

Nothing here ever emits text a model or a repository supplied verbatim: node
ids are always generated (`n0`, `n1`, ...) from stable component order, and
every label is passed through `_safe_label`, which strips Mermaid/HTML
metacharacters before it is ever wrapped in quotes. `validate()` is an
independent allowlist check applied both to this module's own output and to
the curated OptiLearn detail diagram (hand-authored, but still read as
untrusted text on load — see app/architecture/curated.py).
"""

import re
import unicodedata

from app.config import (
    ARCH_MAX_MERMAID_CHARS,
    ARCH_MAX_MERMAID_EDGES,
    ARCH_MAX_MERMAID_NODES,
)
from app.models.contracts import (
    ArchitectureComponent,
    ArchitectureRelationship,
    ArchitectureReport,
)

_REMOVE_CHARS = "<>&{}[]|\\`;%#$@~^\"'"
_REMOVE_TABLE = str.maketrans("", "", _REMOVE_CHARS)
_ZERO_WIDTH_RE = re.compile("[\u200b-\u200f\u202a-\u202e\u2066-\u2069]")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WS_RE = re.compile(r"\s+")


def _safe_label(text: str, max_len: int = 48) -> str:
    """Sanitize arbitrary text into a value safe to wrap in double quotes
    inside a Mermaid node/edge label. Never raises; empty input -> a fallback."""
    text = unicodedata.normalize("NFKC", text or "")
    text = _CONTROL_RE.sub("", text)
    text = _ZERO_WIDTH_RE.sub("", text)
    text = text.translate(_REMOVE_TABLE)
    text = _WS_RE.sub(" ", text).strip()
    if not text:
        return "unlabeled"
    if len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return text


def _safe_edge_label(text: str, max_len: int = 24) -> str:
    label = _safe_label(text, max_len)
    return "" if label == "unlabeled" else label


def _node_line(node_id: str, label: str, is_data_store: bool) -> str:
    safe = _safe_label(label)
    if is_data_store:
        return f'    {node_id}[("{safe}")]'
    return f'    {node_id}["{safe}"]'


def generate_from_parts(
    components: list[ArchitectureComponent],
    relationships: list[ArchitectureRelationship],
) -> str:
    """Pure function: same inputs always produce the same output string."""
    if not components:
        return ""

    ordered = sorted(components, key=lambda c: (-c.rank, c.id))
    node_cap = min(ARCH_MAX_MERMAID_NODES, len(ordered))

    while node_cap > 0:
        kept = ordered[:node_cap]
        dropped = len(ordered) - node_cap
        id_to_node = {c.id: f"n{i}" for i, c in enumerate(kept)}

        lines = ["graph TD"]
        for i, comp in enumerate(kept):
            lines.append(_node_line(f"n{i}", comp.label, comp.type == "data_store"))
        if dropped > 0:
            more_id = f"n{len(kept)}"
            lines.append(_node_line(more_id, f"+{dropped} more component(s)", False))

        edge_lines: list[str] = []
        seen_edges: set[tuple[str, str]] = set()
        rel_sorted = sorted(
            relationships,
            key=lambda r: (
                id_to_node.get(r.source, "z"),
                id_to_node.get(r.target, "z"),
                r.type,
            ),
        )
        for rel in rel_sorted:
            src, dst = id_to_node.get(rel.source), id_to_node.get(rel.target)
            if not src or not dst or src == dst or (src, dst) in seen_edges:
                continue
            seen_edges.add((src, dst))
            label = _safe_edge_label(rel.type)
            edge_lines.append(
                f"    {src} -->|{label}| {dst}" if label else f"    {src} --> {dst}"
            )
            if len(edge_lines) >= ARCH_MAX_MERMAID_EDGES:
                break

        text = "\n".join([*lines, *edge_lines])
        if len(text) <= ARCH_MAX_MERMAID_CHARS or node_cap == 1:
            return text
        node_cap -= 1
    return "graph TD"


def generate(report: ArchitectureReport) -> str:
    return generate_from_parts(report.components, report.relationships)


# ── Validation (allowlist) ────────────────────────────────────────────────────
# Applied to generated output AND to the curated detail diagram. Intentionally
# conservative: anything not matching a known-safe line shape is rejected.

_ID = r"[A-Za-z_][A-Za-z0-9_-]*"
_QUOTED = r'"[^"<>]*"'

_LINE_PATTERNS = [
    re.compile(p)
    for p in (
        r"^graph (TD|TB|LR)$",
        r"^direction (TB|LR)$",
        rf"^subgraph {_ID}\[{_QUOTED}\]$",
        r"^end$",
        rf"^{_ID}\[{_QUOTED}\]$",
        rf"^{_ID}\[\({_QUOTED}\)\]$",
        rf"^{_ID} --> {_ID}$",
        rf"^{_ID} -->\|[^|<>]{{0,100}}\| {_ID}$",
    )
]


def validate(text: str) -> list[str]:
    """
    [] means safe. Otherwise a list of human-readable problems.

    Pure allowlist: a line survives only if it matches one of the 8 known-safe
    shapes in _LINE_PATTERNS. This deliberately has no separate deny-list for
    tokens like `click`, `href`, `%%{`, `classDef` — those words remain legal
    as *inert text inside a quoted label* (harmless: Mermaid only interprets
    them as directives at the start of their own line, a shape nothing in
    _LINE_PATTERNS admits), while an actual `click <id> "url"` directive line,
    an `%%{init}%%` block, or a `<script>` tag (excluded from _QUOTED by its
    own `[^"<>]` character class) can never match any allowed shape and is
    rejected as unrecognised syntax below. A separate word-based deny-list
    would only add false positives on legitimate label text without closing
    any gap the allowlist leaves open.
    """
    if not text.strip():
        return []
    problems: list[str] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        if not any(pat.match(line) for pat in _LINE_PATTERNS):
            problems.append(f"line {lineno}: unrecognised Mermaid syntax: {raw!r}")
    return problems
