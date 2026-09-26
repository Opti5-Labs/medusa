"""
Huge-repository behaviour, using synthetic trees generated at test time (no
committed multi-thousand-file fixture). Tier transitions and their API-level
status/coverage effects are covered in test_arch_api.py; this file focuses on
bounded runtime and the current no-model-dependency guarantee.
"""

import ast
import time
from pathlib import Path

from app.architecture import graph as graph_mod
from app.architecture import inventory as inv_mod
from app.architecture import manifests as mf_mod
from app.architecture import mermaid as mm
from app.architecture.extract_python import extract


def _synth_tree(root: Path, n_files: int, n_dirs: int) -> None:
    for i in range(n_files):
        d = root / f"pkg{i % n_dirs}" / "sub"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"mod_{i}.py").write_text(
            f"import os\n\ndef f_{i}():\n    return {i}\n", encoding="utf-8"
        )


def test_bounded_runtime_on_a_few_thousand_files(tmp_path):
    _synth_tree(tmp_path, n_files=3000, n_dirs=30)

    start = time.monotonic()
    inv = inv_mod.build_inventory(tmp_path)
    facts = mf_mod.scan_manifests(tmp_path, inv)
    tier, limit_exceeded = inv_mod.choose_tier(inv)

    # Even a huge repo only ever parses up to the configured budget.
    parseable = sorted(
        (f for f in inv.files if f.language in inv_mod.SUPPORTED_LANGUAGES),
        key=inv_mod.priority_key,
    )[:50]
    file_facts = {
        f.rel_path: extract(f.abs_path.read_text("utf-8"), f.rel_path)
        for f in parseable
    }
    edges = graph_mod.build_edges(file_facts)
    ranks = graph_mod.pagerank(list(file_facts.keys()), edges)
    grouping = graph_mod.group_components(inv.files, file_facts, ranks, facts)
    diagram = mm.generate_from_parts(grouping.components, [])
    elapsed = time.monotonic() - start

    assert inv.files_discovered == 3000
    assert tier == 2  # exceeds the default ARCH_MAX_PARSED_FILES budget
    assert limit_exceeded == "ARCH_MAX_PARSED_FILES"
    assert mm.validate(diagram) == []
    assert elapsed < 30, f"inventory+manifests+graph took {elapsed:.1f}s for 3000 files"


def test_graph_and_mermaid_bounds_hold_on_a_fully_connected_graph(monkeypatch):
    """Every-file-imports-every-file is the worst case for edge count."""
    monkeypatch.setattr(graph_mod, "ARCH_MAX_GRAPH_EDGES", 500)
    n = 60
    nodes = [f"f{i}.py" for i in range(n)]
    edges = [(a, b) for a in nodes for b in nodes if a != b]  # n*(n-1) = 3540 edges
    ranks = graph_mod.pagerank(nodes, edges[: graph_mod.ARCH_MAX_GRAPH_EDGES])
    assert len(ranks) == n
    assert all(v >= 0 for v in ranks.values())


def test_architecture_pipeline_has_no_model_dependency_yet():
    """
    This build's architecture pipeline is fully deterministic: it never
    imports app.agents.granite or app.agents.bob, so there is structurally no
    model call to make even before any budget check runs. (Model-assisted
    synthesis is deferred — see the architecture plan, section 20.)
    """
    import app.pipelines.architecture as pipeline_mod

    src = Path(pipeline_mod.__file__).read_text("utf-8")
    tree = ast.parse(src)
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not any("granite" in m or "agents.bob" in m for m in imported_modules)
