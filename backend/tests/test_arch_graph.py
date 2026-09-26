"""Tests for app/architecture/graph.py — module resolution, PageRank, grouping."""

from app.architecture import graph as graph_mod
from app.architecture.extract_python import extract
from app.architecture.inventory import FileRecord
from app.architecture.manifests import ManifestFacts

# ── module resolution ─────────────────────────────────────────────────────────


def test_module_key_handles_init_files():
    assert graph_mod._module_key("app/services/db.py") == "app.services.db"
    assert graph_mod._module_key("app/services/__init__.py") == "app.services"


def test_resolve_absolute_import():
    index = graph_mod.build_module_index(["app/services/db.py", "app/routes/users.py"])
    target = graph_mod.resolve_import(
        index, "app/routes/users.py", "app.services.db", 0
    )
    assert target == "app/services/db.py"


def test_resolve_relative_import_single_dot():
    index = graph_mod.build_module_index(["app/routes/users.py", "app/routes/util.py"])
    # "from . import util" inside app/routes/users.py
    target = graph_mod.resolve_import(index, "app/routes/users.py", "util", 1)
    assert target == "app/routes/util.py"


def test_resolve_relative_import_parent_package():
    index = graph_mod.build_module_index(["app/routes/users.py", "app/core/config.py"])
    # "from ..core import config" inside app/routes/users.py
    target = graph_mod.resolve_import(index, "app/routes/users.py", "core.config", 2)
    assert target == "app/core/config.py"


def test_self_import_is_never_resolved():
    index = graph_mod.build_module_index(["app/main.py"])
    assert graph_mod.resolve_import(index, "app/main.py", "app.main", 0) is None


# ── PageRank ──────────────────────────────────────────────────────────────────


def test_pagerank_empty_graph():
    assert graph_mod.pagerank([], []) == {}


def test_pagerank_ranks_hub_above_leaf():
    nodes = ["a", "b", "c"]
    edges = [("a", "c"), ("b", "c")]  # c is imported by both a and b
    ranks = graph_mod.pagerank(nodes, edges)
    assert ranks["c"] > ranks["a"]
    assert ranks["c"] > ranks["b"]


def test_pagerank_is_deterministic():
    nodes = ["a", "b", "c", "d"]
    edges = [("a", "b"), ("b", "c"), ("c", "a"), ("d", "a")]
    r1 = graph_mod.pagerank(nodes, edges)
    r2 = graph_mod.pagerank(nodes, edges)
    assert r1 == r2


def test_pagerank_sums_to_approximately_one():
    nodes = ["a", "b", "c"]
    edges = [("a", "b"), ("b", "c"), ("c", "a")]
    ranks = graph_mod.pagerank(nodes, edges)
    assert abs(sum(ranks.values()) - 1.0) < 1e-6


# ── grouping ──────────────────────────────────────────────────────────────────


def _record(rel_path: str) -> FileRecord:
    return FileRecord(
        rel_path=rel_path,
        abs_path=None,  # not read during grouping
        ext=".py",
        size=10,
        lines=1,
        language="Python",
        is_config_doc=False,
        is_test=False,
        is_generated=False,
    )


def test_deepest_segment_wins_over_generic_top_level_package_name():
    """Regression: 'app' is a near-universal top-level Python package name.
    Before the fix, checking segments shallowest-first meant every file under
    app/ (including app/routes/*, app/services/*) collapsed into one bucket
    matched by 'app' itself, if 'app' were ever a rule (it no longer is) —
    this asserts the deepest, most specific directory always wins."""
    files = [
        _record("app/routes/users.py"),
        _record("app/routes/orders.py"),
        _record("app/services/db.py"),
        _record("app/services/cache.py"),
    ]
    facts = {f.rel_path: extract("x = 1\n", f.rel_path) for f in files}
    ranks = {f.rel_path: 0.1 for f in files}
    result = graph_mod.group_components(files, facts, ranks, ManifestFacts())
    types_by_id = {c.id: c.type for c in result.components}
    labels_by_id = {c.id: c.label for c in result.components}
    assert set(types_by_id.values()) == {"api_layer", "service"}
    assert (
        "routes" in "".join(labels_by_id.values()).lower()
        or "api" in "".join(labels_by_id.values()).lower()
    )


def test_component_below_min_files_is_dropped_unless_entrypoint():
    files = [_record("app/tools/lonely.py")]
    result = graph_mod.group_components(files, {}, {}, ManifestFacts())
    # A single file with no matching directory rule and no declared
    # entrypoint falls below ARCH_MIN_COMPONENT_FILES and is dropped.
    assert result.components == []


def test_entrypoint_survives_single_file_minimum():
    files = [_record("app/main.py")]
    mf = ManifestFacts()
    mf.entrypoints.append({"path": "app/main.py", "kind": "http_server", "detail": ""})
    result = graph_mod.group_components(files, {}, {}, mf)
    assert len(result.components) == 1
    assert result.components[0].file_count == 1


def test_component_cap_folds_overflow_into_other_modules(monkeypatch):
    monkeypatch.setattr(graph_mod, "ARCH_MAX_COMPONENTS", 1)
    files = [
        _record("app/routes/a.py"),
        _record("app/routes/b.py"),
        _record("app/services/c.py"),
        _record("app/services/d.py"),
    ]
    result = graph_mod.group_components(files, {}, {}, ManifestFacts())
    assert len(result.components) == 2  # 1 kept + 1 "Other modules"
    other = next(c for c in result.components if "Other modules" in c.label)
    assert other.file_count == 2


def test_relationships_deduplicated_and_confidence_scales_with_count():
    edges = [("a.py", "b.py"), ("a2.py", "b.py")]
    path_to_component = {"a.py": "comp_a", "a2.py": "comp_a", "b.py": "comp_b"}
    rels = graph_mod.build_relationships(edges, path_to_component)
    assert len(rels) == 1
    assert rels[0].source == "comp_a"
    assert rels[0].target == "comp_b"
    assert rels[0].confidence == 0.7  # 0.5 + 0.1 * 2


def test_relationships_never_self_referential_from_same_component():
    edges = [("a.py", "b.py")]
    path_to_component = {"a.py": "same", "b.py": "same"}
    rels = graph_mod.build_relationships(edges, path_to_component)
    assert rels == []
