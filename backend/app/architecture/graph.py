"""
Deterministic intermediate graph: resolve imports between parsed files, rank
files with a small PageRank implementation (Aider's repo-map design, no
`networkx` dependency), then group files into architecture-level components
and derive component-to-component relationships.

Everything here operates on repository-relative paths already produced by
app.architecture.inventory / extract_python — no filesystem access, no
network, fully deterministic given the same inputs.
"""

import posixpath
import re
from dataclasses import dataclass

from app.architecture.extract_python import FileFacts
from app.architecture.inventory import FileRecord
from app.architecture.manifests import ManifestFacts
from app.config import (
    ARCH_MAX_COMPONENTS,
    ARCH_MAX_GRAPH_EDGES,
    ARCH_MAX_GRAPH_NODES,
    ARCH_MAX_RELATIONSHIPS,
    ARCH_MIN_COMPONENT_FILES,
)
from app.models.contracts import (
    ArchitectureComponent,
    ArchitectureRelationship,
    EvidenceRef,
)

# ── Module resolution ─────────────────────────────────────────────────────────


def _module_key(rel_path: str) -> str:
    """'app/services/db.py' -> 'app.services.db'; '.../__init__.py' -> the package."""
    p = rel_path.removesuffix(".py")
    if p.endswith("/__init__"):
        p = p[: -len("/__init__")]
    elif p == "__init__":
        p = ""
    return p.replace("/", ".")


def build_module_index(rel_paths: list[str]) -> dict[str, str]:
    """
    dotted-name -> rel_path, registering every dotted suffix ("app.services.db",
    "services.db", "db") so both absolute and shallower import styles resolve.
    The first (sorted) file to claim a given suffix wins ties.
    """
    index: dict[str, str] = {}
    for rp in sorted(rel_paths):
        full = _module_key(rp)
        if not full:
            continue
        parts = full.split(".")
        for i in range(len(parts)):
            suffix = ".".join(parts[i:])
            index.setdefault(suffix, rp)
    return index


def resolve_relative(importer_rel_path: str, level: int, module: str | None) -> str:
    """Approximate Python's relative-import resolution (`from . import x`, `from ..a import b`)."""
    importer_key = _module_key(importer_rel_path)
    parts = importer_key.split(".") if importer_key else []
    package = parts[:-1]  # the package this module lives in
    if level > 1:
        cut = level - 1
        package = package[:-cut] if cut <= len(package) else []
    target = [*package, *module.split(".")] if module else package
    return ".".join(p for p in target if p)


def resolve_import(
    index: dict[str, str], importer_rel_path: str, module: str | None, level: int
) -> str | None:
    if level > 0:
        dotted = resolve_relative(importer_rel_path, level, module)
    else:
        dotted = module or ""
    if not dotted:
        return None
    target = index.get(dotted)
    if target and target != importer_rel_path:
        return target
    # `from a.b import c` where c is itself a submodule a.b.c
    if module and level == 0:
        parts = dotted.split(".")
        target = index.get(".".join(parts))
    return target if target != importer_rel_path else None


def build_edges(file_facts: dict[str, FileFacts]) -> list[tuple[str, str]]:
    """Directed (importer, imported) edges between parsed files only."""
    index = build_module_index(list(file_facts.keys()))
    edges: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for rel_path, facts in file_facts.items():
        for module, level, _lineno in facts.imports:
            target = resolve_import(index, rel_path, module, level)
            if target and target != rel_path and (rel_path, target) not in seen:
                seen.add((rel_path, target))
                edges.append((rel_path, target))
        for expr in facts.router_includes:
            # Best-effort: "auth.router" -> try resolving "auth" as a module.
            head = expr.split(".", 1)[0]
            target = index.get(head)
            if target and target != rel_path and (rel_path, target) not in seen:
                seen.add((rel_path, target))
                edges.append((rel_path, target))
    return edges[:ARCH_MAX_GRAPH_EDGES]


# ── PageRank (Aider-style: power iteration, no networkx) ─────────────────────


def pagerank(
    nodes: list[str],
    edges: list[tuple[str, str]],
    damping: float = 0.85,
    iterations: int = 30,
    tol: float = 1e-6,
) -> dict[str, float]:
    """Deterministic power-iteration PageRank. Empty graph -> {}."""
    nodes = sorted(set(nodes))[:ARCH_MAX_GRAPH_NODES]
    n = len(nodes)
    if n == 0:
        return {}
    idx = {node: i for i, node in enumerate(nodes)}
    adj: list[list[int]] = [[] for _ in range(n)]
    outdeg = [0] * n
    for a, b in edges:
        if a in idx and b in idx and a != b:
            ai, bi = idx[a], idx[b]
            adj[ai].append(bi)
            outdeg[ai] += 1

    ranks = [1.0 / n] * n
    for _ in range(iterations):
        dangling_sum = sum(ranks[i] for i in range(n) if outdeg[i] == 0)
        new = [(1 - damping) / n + damping * dangling_sum / n] * n
        for a in range(n):
            if outdeg[a] == 0:
                continue
            share = damping * ranks[a] / outdeg[a]
            for b in adj[a]:
                new[b] += share
        delta = sum(abs(new[i] - ranks[i]) for i in range(n))
        ranks = new
        if delta < tol:
            break
    return {node: ranks[idx[node]] for node in nodes}


# ── Component grouping ────────────────────────────────────────────────────────

_DIR_TYPE_RULES: list[tuple[frozenset[str], str, str]] = [
    (
        frozenset({"api", "routes", "routers", "controllers", "endpoints"}),
        "api_layer",
        "API layer",
    ),
    (frozenset({"services", "agents", "usecases", "domain"}), "service", "Services"),
    (
        frozenset({"models", "schemas", "entities", "migrations"}),
        "data_store",
        "Data models",
    ),
    (frozenset({"components", "ui", "views", "pages"}), "frontend", "UI / pages"),
    (frozenset({"workers", "tasks", "jobs"}), "worker", "Background workers"),
    (frozenset({"config", "settings", "core"}), "config", "Configuration"),
    (frozenset({"tests", "test", "__tests__", "spec"}), "tests", "Tests"),
    (
        frozenset({"deploy", "infra", "terraform", "k8s", "deployment"}),
        "infrastructure",
        "Deployment",
    ),
    (frozenset({"docs", "doc"}), "docs", "Documentation"),
]
_DIR_RULE_BY_SEGMENT: dict[str, tuple[str, str]] = {
    seg: (ctype, label) for segs, ctype, label in _DIR_TYPE_RULES for seg in segs
}

_SLUG_RE = re.compile(r"[^a-z0-9_-]+")

# Grouping key type for components produced by _split_dominant_package.
_MODULE = "module"
_SPLIT_MIN_MODULES = 3


def _slugify(text: str, used: set[str]) -> str:
    base = _SLUG_RE.sub("_", text.lower()).strip("_-")[:48] or "component"
    if not base[0].isalnum():
        base = f"c_{base}"[:48]
    slug = base
    n = 2
    while slug in used:
        suffix = f"-{n}"
        slug = f"{base[: 48 - len(suffix)]}{suffix}"
        n += 1
    used.add(slug)
    return slug


def _package_root_for(rel_path: str, package_roots: list[str]) -> str:
    best = ""
    for root in package_roots:
        prefix = f"{root}/" if root else ""
        if rel_path.startswith(prefix) and len(root) >= len(best):
            best = root
    return best


def _group_key(rel_path: str, package_root: str) -> tuple[str, str, str]:
    """
    (package_root, component_type, label) grouping key for one file.

    Directory segments are checked deepest-first: a file under
    'app/api/routes/users.py' should match 'routes' (specific), not 'app'
    (a near-universal top-level Python/Node package name that would
    otherwise swallow every rule beneath it if checked shallowest-first).
    """
    within = rel_path[len(package_root) + 1 :] if package_root else rel_path
    segments = within.split("/")[:-1]  # directories only, not the filename
    for seg in reversed(segments):
        rule = _DIR_RULE_BY_SEGMENT.get(seg.lower())
        if rule:
            return (package_root, rule[0], rule[1])
    fallback = segments[0] if segments else "root"
    return (package_root, "unknown", fallback)


def _common_dir(paths: list[str]) -> str:
    """Deepest directory shared by every path ("" when they only share the root)."""
    dirs = [p.rsplit("/", 1)[0] if "/" in p else "" for p in paths]
    if any(d == "" for d in dirs):
        return ""
    return posixpath.commonpath(dirs)


def _split_dominant_package(
    groups: dict[tuple[str, str, str], list[FileRecord]],
    file_facts: dict[str, FileFacts],
) -> dict[tuple[str, str, str], list[FileRecord]]:
    """
    Directory grouping works when a repo has several top-level areas (api/,
    services/, db/...). A library usually keeps all its code in one package
    (src/<pkg>/*.py), which would collapse into a single box with no edges. When
    one plain directory group holds most of the parsed source, split it into one
    component per module (file) or subpackage directly under that package.
    """
    parsed_total = sum(
        1 for ms in groups.values() for m in ms if m.rel_path in file_facts
    )
    for key, members in groups.items():
        pkg, ctype, label = key
        if ctype != "unknown":
            continue
        source = [m for m in members if m.rel_path in file_facts]
        if len(source) < _SPLIT_MIN_MODULES or 2 * len(source) < parsed_total:
            continue
        base = _common_dir([m.rel_path for m in source])
        package_name = base.rsplit("/", 1)[-1] if base else ""
        buckets: dict[str, list[FileRecord]] = {}
        for m in members:
            prefix = f"{base}/" if base else ""
            rest = m.rel_path[len(prefix) :] if m.rel_path.startswith(prefix) else ""
            parts = rest.split("/") if rest else []
            if len(parts) > 1:
                name = parts[0]  # a subpackage
            elif parts and parts[0].endswith(".py") and parts[0] != "__init__.py":
                name = parts[0][: -len(".py")]  # a module
            else:
                name = ""  # __init__.py, py.typed and other package-level files
            buckets.setdefault(name, []).append(m)
        modules = [n for n in buckets if n]
        if len(modules) < _SPLIT_MIN_MODULES:
            continue
        split = {k: v for k, v in groups.items() if k != key}
        for name, ms in buckets.items():
            if name:
                module_label = f"{package_name}.{name}" if package_name else name
            else:
                module_label = package_name or label
            split[(pkg, _MODULE, module_label)] = ms
        return split  # only the dominant group is split
    return groups


@dataclass
class GroupingResult:
    components: list[ArchitectureComponent]
    path_to_component: dict[str, str]


def group_components(
    files: list[FileRecord],
    file_facts: dict[str, FileFacts],
    ranks: dict[str, float],
    manifest_facts: ManifestFacts,
) -> GroupingResult:
    package_roots = sorted(manifest_facts.package_roots, key=len, reverse=True)
    entry_paths = {e["path"] for e in manifest_facts.entrypoints}

    groups: dict[tuple[str, str, str], list[FileRecord]] = {}
    for f in files:
        if f.is_generated:
            continue
        pkg = _package_root_for(f.rel_path, package_roots)
        key = _group_key(f.rel_path, pkg)
        groups.setdefault(key, []).append(f)
    groups = _split_dominant_package(groups, file_facts)

    multi_package = len({k[0] for k in groups}) > 1
    used_ids: set[str] = set()
    components: list[ArchitectureComponent] = []
    path_to_component: dict[str, str] = {}

    scored_groups = []
    for key, members in groups.items():
        pkg, ctype, label = key
        file_count = len(members)
        has_entrypoint = any(m.rel_path in entry_paths for m in members)
        # A module is meaningful on its own; other groups need a few files.
        if (
            file_count < ARCH_MIN_COMPONENT_FILES
            and not has_entrypoint
            and ctype != _MODULE
        ):
            continue
        rank_sum = sum(ranks.get(m.rel_path, 0.0) for m in members)
        scored_groups.append((key, members, rank_sum, has_entrypoint))

    scored_groups.sort(key=lambda t: (-t[2], t[0]))

    kept = scored_groups[:ARCH_MAX_COMPONENTS]
    overflow = scored_groups[ARCH_MAX_COMPONENTS:]

    for key, members, rank_sum, has_entrypoint in kept:
        pkg, ctype, label = key
        members_sorted = sorted(
            members, key=lambda m: (-ranks.get(m.rel_path, 0.0), m.rel_path)
        )
        display_label = f"{pkg}: {label}" if multi_package and pkg else label
        comp_id = _slugify(f"{pkg}_{label}" if pkg else label, used_ids)
        is_module = ctype == _MODULE
        matched_rule = ctype not in ("unknown", _MODULE)
        confidence = 0.8 if matched_rule else 0.6 if is_module else 0.5
        paths_sample = [m.rel_path for m in members_sorted[:10]]
        evidence = [
            EvidenceRef(path=m.rel_path, note="representative file in this component")
            for m in members_sorted[:3]
        ]
        parsed_count = sum(1 for m in members if m.rel_path in file_facts)
        description = (
            f"{file_count} file(s)"
            + (f" under {pkg}/" if pkg else "")
            + (
                f" matching the '{label}' convention"
                if matched_rule
                else f" in module or subpackage '{label}'"
                if is_module
                else " grouped by directory"
            )
            + (
                f"; {parsed_count} parsed for imports and routes"
                if parsed_count
                else ""
            )
            + "."
        )
        component = ArchitectureComponent(
            id=comp_id,
            label=display_label[:60],
            type="library" if is_module else ctype,
            description=description,
            paths=paths_sample,
            evidence=evidence,
            confidence=confidence,
            assisted_by="deterministic",
            file_count=file_count,
            rank=rank_sum,
        )
        components.append(component)
        for m in members:
            path_to_component[m.rel_path] = comp_id

    if overflow:
        other_count = sum(len(members) for _, members, _, _ in overflow)
        other_paths = []
        for _, members, _, _ in sorted(overflow, key=lambda t: -t[2])[:1]:
            other_paths = [m.rel_path for m in members[:10]]
        comp_id = _slugify("other_modules", used_ids)
        component = ArchitectureComponent(
            id=comp_id,
            label=f"Other modules ({other_count} files)",
            type="unknown",
            description=(
                f"{other_count} additional file(s) across {len(overflow)} smaller "
                f"group(s), folded together to keep the diagram readable."
            ),
            paths=other_paths,
            evidence=[],
            confidence=0.3,
            assisted_by="deterministic",
            file_count=other_count,
            rank=0.0,
        )
        components.append(component)
        for _, members, _, _ in overflow:
            for m in members:
                path_to_component[m.rel_path] = comp_id

    return GroupingResult(components=components, path_to_component=path_to_component)


def build_relationships(
    edges: list[tuple[str, str]],
    path_to_component: dict[str, str],
) -> list[ArchitectureRelationship]:
    counts: dict[tuple[str, str], int] = {}
    examples: dict[tuple[str, str], list[str]] = {}
    for a, b in edges:
        ca, cb = path_to_component.get(a), path_to_component.get(b)
        if not ca or not cb or ca == cb:
            continue
        key = (ca, cb)
        counts[key] = counts.get(key, 0) + 1
        examples.setdefault(key, [])
        if len(examples[key]) < 2 and a not in examples[key]:
            examples[key].append(a)

    relationships = [
        ArchitectureRelationship(
            source=src,
            target=dst,
            type="imports",
            explanation=f"{count} import reference(s) from files in '{src}' to files in '{dst}'.",
            evidence=[
                EvidenceRef(path=p, note="contains a resolved import")
                for p in examples[(src, dst)]
            ],
            confidence=min(1.0, 0.5 + 0.1 * count),
            assisted_by="deterministic",
        )
        for (src, dst), count in counts.items()
    ]
    relationships.sort(key=lambda r: (-r.confidence, r.source, r.target))
    return relationships[:ARCH_MAX_RELATIONSHIPS]
