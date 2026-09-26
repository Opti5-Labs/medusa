"""
Security tests for the architecture pipeline: path traversal, secret leakage,
Mermaid injection via repository content, symlink escapes, and absolute-path
leakage. All offline, all synthetic trees.
"""

import io
import json
import zipfile

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import architecture as arch_module
from app.api import scan as scan_module
from app.architecture import graph as graph_mod
from app.architecture import inventory as inv_mod
from app.architecture import manifests as mf_mod
from app.architecture import mermaid as mm
from app.main import app
from app.models.contracts import ArchitectureComponent
from app.pipelines.context import safe_path
from app.ratelimit import RateLimiter
from app.store import RunStore


def _write(root, rel, content=""):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


# ── path traversal ────────────────────────────────────────────────────────────


def test_safe_path_rejects_traversal_outside_root(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (tmp_path / "secret.txt").write_text("outside", encoding="utf-8")
    assert safe_path(root, "../secret.txt") is None
    assert safe_path(root, "../../etc/passwd") is None
    assert safe_path(root, "/etc/passwd") is None


def test_walk_never_escapes_root_via_relative_path(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _write(root, "app/main.py", "x = 1\n")
    inv = inv_mod.build_inventory(root)
    for f in inv.files:
        assert f.abs_path.resolve().is_relative_to(root.resolve())


# ── secrets ───────────────────────────────────────────────────────────────────


def test_env_value_never_appears_anywhere_in_manifest_output(tmp_path):
    root = tmp_path
    secret = "sk-super-secret-value-should-never-leak"
    _write(root, ".env", f"API_KEY={secret}\n")
    _write(root, ".env.example", "API_KEY=\n")
    inv = inv_mod.build_inventory(root)
    facts = mf_mod.scan_manifests(root, inv)
    blob = json.dumps(
        {
            "env": facts.env_var_names,
            "deployment": facts.deployment,
            "frameworks": list(facts.frameworks),
        }
    )
    assert secret not in blob
    assert "API_KEY" in facts.env_var_names


# ── mermaid injection via repository / model content ─────────────────────────


def test_hostile_component_label_cannot_inject_mermaid_syntax():
    """
    The words 'click'/'href' may legitimately survive as inert English text
    *inside* the one quoted label the sanitizer produces (they are not
    Mermaid syntax there) — what must never happen is the label breaking out
    of its quotes to form an actual `click <id> "url"` directive line. That
    structural property is exactly what validate() checks.
    """
    hostile = 'x"] --> evil[click href "http://evil.example" "pwn'
    comp = ArchitectureComponent(id="c1", label=hostile, type="service", confidence=0.5)
    text = mm.generate_from_parts([comp], [])
    assert mm.validate(text) == []
    assert text.count('"') == 2  # exactly one label, still wrapped in one quote pair
    assert not any(line.strip().startswith("click ") for line in text.splitlines())


def test_hostile_directory_name_cannot_break_out_of_label(tmp_path):
    # `"`, `<`, `>` etc. are illegal in filenames on Windows, so this uses
    # only characters that are legal there and still Mermaid-unsafe.
    root = tmp_path
    _write(root, "weird];click n0 [evil/services/a.py", "x = 1\n")
    _write(root, "weird];click n0 [evil/services/b.py", "x = 1\n")
    inv = inv_mod.build_inventory(root)
    facts = mf_mod.scan_manifests(root, inv)
    ranks = {}
    grouping = graph_mod.group_components(inv.files, {}, ranks, facts)
    text = mm.generate_from_parts(grouping.components, [])
    assert mm.validate(text) == []
    assert "click" not in text


def test_mermaid_directive_in_repo_text_never_reaches_diagram(tmp_path):
    root = tmp_path
    _write(
        root,
        "app/evil/__init__.py",
        '"""%%{init: {"securityLevel": "loose"}}%%"""\n',
    )
    _write(root, "app/evil/mod.py", "x = 1\n")
    inv = inv_mod.build_inventory(root)
    facts = mf_mod.scan_manifests(root, inv)
    grouping = graph_mod.group_components(inv.files, {}, {}, facts)
    text = mm.generate_from_parts(grouping.components, [])
    assert "%%{" not in text
    assert mm.validate(text) == []


# ── symlinks ──────────────────────────────────────────────────────────────────


def test_symlinked_file_is_not_read(tmp_path):
    outside = tmp_path / "outside.py"
    outside.write_text("SECRET = 1\n", encoding="utf-8")
    root = tmp_path / "repo"
    root.mkdir()
    link = root / "linked.py"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable/unprivileged on this platform")
    inv = inv_mod.build_inventory(root)
    assert inv.files == []


# ── absolute-path leakage, end to end through the API ────────────────────────


@pytest.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=True),
        base_url="http://test",
    ) as c:
        store = RunStore()
        scan_module.set_store(store)
        arch_module.set_store(store)
        scan_module._scan_limiter = RateLimiter(max_calls=1000, window_seconds=600)
        arch_module._arch_limiter = RateLimiter(max_calls=1000, window_seconds=600)
        store.start()
        yield c
        await store.stop()


async def test_no_absolute_server_path_in_serialized_report(client, tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("app/main.py", "from fastapi import FastAPI\napp = FastAPI()\n")
        zf.writestr("app/services/db.py", "import sqlalchemy\n")
    r = await client.post(
        "/api/scan/upload", files={"file": ("r.zip", buf.getvalue(), "application/zip")}
    )
    scan_id = r.json()["scan_id"]
    r2 = await client.post(f"/api/scans/{scan_id}/architecture")
    arch_id = r2.json()["architecture_id"]

    resp = await client.get(f"/api/architecture/{arch_id}/events")
    text = resp.text
    assert "medusa_zip_" not in text  # the temp-dir prefix used by ingest/zip_upload.py
    assert "Users" not in text
    assert "C:\\\\" not in text and "C:\\" not in text
    assert "/tmp/" not in text
