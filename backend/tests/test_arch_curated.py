"""
Tests for app/architecture/curated.py — the curated OptiLearn artifact.

Verifies the acceptance criteria from the architecture plan: deterministic,
no model dependency, valid Mermaid, evidence paths that exist, and loud
failure on corruption.
"""

import json

import pytest

from app.architecture import curated as curated_mod
from app.architecture import mermaid as mm
from app.config import OPTILEARN_SRC
from app.pipelines.context import safe_path


@pytest.fixture(autouse=True)
def _clear_cache():
    curated_mod._load.cache_clear()
    yield
    curated_mod._load.cache_clear()


def test_load_curated_succeeds_offline_with_no_model_configured(monkeypatch):
    # No env at all is required for this to work.
    report = curated_mod.load_curated("scan-1")
    assert report.status == "complete"
    assert report.source == "curated"
    assert report.repo_source == "demo"


def test_component_and_relationship_counts_match_manifest():
    manifest = json.loads((curated_mod._DIR / "manifest.json").read_text("utf-8"))
    report = curated_mod.load_curated("scan-1")
    assert len(report.components) == manifest["expected_components"]
    assert len(report.relationships) == manifest["expected_relationships"]


def test_every_verified_evidence_path_exists_under_bundled_source():
    report = curated_mod.load_curated("scan-1")
    verified_paths = [
        ev.path for comp in report.components for ev in comp.evidence if ev.verified
    ]
    assert len(verified_paths) >= 7  # the 7 real bundled modules
    for path in verified_paths:
        assert safe_path(OPTILEARN_SRC, path) is not None, (
            f"missing bundled file: {path}"
        )


def test_unverified_evidence_carries_an_upstream_url():
    report = curated_mod.load_curated("scan-1")
    unverified = [
        ev for comp in report.components for ev in comp.evidence if not ev.verified
    ]
    assert unverified  # some components are upstream-only
    assert all(ev.url and ev.url.startswith("https://") for ev in unverified)


def test_generated_overview_mermaid_is_valid_and_present():
    report = curated_mod.load_curated("scan-1")
    assert report.mermaid
    assert mm.validate(report.mermaid) == []


def test_detail_mermaid_is_valid_and_present():
    report = curated_mod.load_curated("scan-1")
    assert report.detail_mermaid
    assert mm.validate(report.detail_mermaid) == []
    assert report.detail_mermaid.startswith("graph TD")


def test_curated_version_is_set():
    report = curated_mod.load_curated("scan-1")
    assert report.curated_version == "optilearn@1.1.0"


def test_two_loads_are_identical_modulo_volatile_fields():
    r1 = curated_mod.load_curated("scan-a")
    r2 = curated_mod.load_curated("scan-b")
    d1 = r1.model_dump(exclude={"architecture_id", "scan_id", "generated_at"})
    d2 = r2.model_dump(exclude={"architecture_id", "scan_id", "generated_at"})
    assert d1 == d2


def test_fresh_ids_minted_per_call():
    r1 = curated_mod.load_curated("scan-a")
    r2 = curated_mod.load_curated("scan-a")
    assert r1.architecture_id != r2.architecture_id


# ── corruption / integrity ────────────────────────────────────────────────────


def test_corrupted_hash_raises_loudly(tmp_path, monkeypatch):
    # Copy the real artifact, then corrupt architecture.json's content so its
    # hash no longer matches manifest.json.
    import shutil

    fake_dir = tmp_path / "optilearn"
    shutil.copytree(curated_mod._DIR, fake_dir)
    arch_path = fake_dir / "architecture.json"
    data = json.loads(arch_path.read_text("utf-8"))
    data["summary"] = "tampered"
    arch_path.write_text(json.dumps(data), encoding="utf-8")

    monkeypatch.setattr(curated_mod, "_DIR", fake_dir)
    curated_mod._load.cache_clear()
    with pytest.raises(curated_mod.CuratedArtifactError, match="hash"):
        curated_mod.load_curated("scan-1")


def test_missing_file_raises_loudly(tmp_path, monkeypatch):
    import shutil

    fake_dir = tmp_path / "optilearn"
    shutil.copytree(curated_mod._DIR, fake_dir)
    (fake_dir / "detail.mmd").unlink()

    monkeypatch.setattr(curated_mod, "_DIR", fake_dir)
    curated_mod._load.cache_clear()
    with pytest.raises(curated_mod.CuratedArtifactError):
        curated_mod.load_curated("scan-1")


def test_wrong_component_count_raises_loudly(tmp_path, monkeypatch):
    import hashlib
    import shutil

    fake_dir = tmp_path / "optilearn"
    shutil.copytree(curated_mod._DIR, fake_dir)
    arch_path = fake_dir / "architecture.json"
    data = json.loads(arch_path.read_text("utf-8"))
    data["components"].pop()
    new_text = json.dumps(data)
    arch_path.write_text(new_text, encoding="utf-8")

    manifest_path = fake_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["sha256"]["architecture.json"] = hashlib.sha256(
        new_text.encode("utf-8")
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    monkeypatch.setattr(curated_mod, "_DIR", fake_dir)
    curated_mod._load.cache_clear()
    with pytest.raises(curated_mod.CuratedArtifactError, match="components"):
        curated_mod.load_curated("scan-1")


def test_missing_bundled_evidence_file_raises_loudly(tmp_path, monkeypatch):
    """Simulates accidental deletion of a bundled OptiLearn source file that a
    curated component's evidence points to. The curated dir itself is copied
    byte-for-byte (shutil.copytree) so its recorded hashes stay valid; only
    the bundled-source root is redirected to an empty directory."""
    import shutil

    fake_dir = tmp_path / "optilearn"
    shutil.copytree(curated_mod._DIR, fake_dir)
    fake_src = tmp_path / "empty_src"
    fake_src.mkdir()  # none of the real bundled files exist here

    monkeypatch.setattr(curated_mod, "_DIR", fake_dir)
    monkeypatch.setattr(curated_mod, "OPTILEARN_SRC", fake_src)
    curated_mod._load.cache_clear()
    with pytest.raises(curated_mod.CuratedArtifactError, match="missing bundled file"):
        curated_mod.load_curated("scan-1")
