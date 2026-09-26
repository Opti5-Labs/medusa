"""
Integration tests for the architecture API, through the real ASGI app —
POST /api/scans/{scan_id}/architecture, GET .../events, GET .../{id},
GET .../download. Mirrors the conventions in test_runs_api.py.
"""

import io
import json
import zipfile

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import architecture as arch_module
from app.api import scan as scan_module
from app.main import app
from app.ratelimit import RateLimiter
from app.store import RunStore


@pytest.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=True),
        base_url="http://test",
    ) as c:
        store = RunStore()
        scan_module.set_store(store)
        arch_module.set_store(store)
        # These limiters are module-level singletons shared across the whole
        # test session; replace them so this file's many calls never trip
        # RATE_SCANS_PER_WINDOW / RATE_ARCH_PER_WINDOW (see test_runs_api.py).
        scan_module._scan_limiter = RateLimiter(max_calls=1000, window_seconds=600)
        arch_module._arch_limiter = RateLimiter(max_calls=1000, window_seconds=600)
        store.start()
        yield c
        await store.stop()


async def _events(client, url) -> tuple[list[dict], dict]:
    resp = await client.get(url)
    assert resp.status_code == 200
    logs, done, name = [], None, None
    for line in resp.text.splitlines():
        if line.startswith("event:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data = json.loads(line.split(":", 1)[1].strip())
            if name == "log":
                logs.append(data)
            elif name == "done":
                done = data
    assert done is not None, "stream ended without a done event"
    return logs, done


def _make_zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, content in files.items():
            zf.writestr(path, content)
    return buf.getvalue()


_FASTAPI_REPO = {
    "requirements.txt": "fastapi\nsqlalchemy\n",
    "app/__init__.py": "",
    "app/main.py": (
        "from fastapi import FastAPI\n"
        "from app.routes import users\n\n"
        "app = FastAPI()\n"
        "app.include_router(users.router)\n"
    ),
    "app/routes/__init__.py": "",
    "app/routes/users.py": (
        "from fastapi import APIRouter\n"
        "from app.services import db\n\n"
        "router = APIRouter()\n\n"
        "@router.get('/users')\n"
        "async def list_users():\n"
        "    return db.get_users()\n"
    ),
    "app/services/__init__.py": "",
    "app/services/db.py": "import sqlalchemy\n\ndef get_users():\n    return []\n",
}


# ── demo / curated ─────────────────────────────────────────────────────────────


async def test_curated_demo_architecture_end_to_end(client, monkeypatch):
    calls = {"n": 0}

    async def fail_if_called(*a, **kw):
        calls["n"] += 1
        raise AssertionError("Granite must not be called for the curated demo path")

    monkeypatch.setattr("app.agents.granite.chat_json", fail_if_called)

    r = await client.post("/api/scan", json={"source": "demo"})
    scan_id = r.json()["scan_id"]

    r2 = await client.post(f"/api/scans/{scan_id}/architecture")
    assert r2.status_code == 200
    arch_id = r2.json()["architecture_id"]

    logs, done = await _events(client, f"/api/architecture/{arch_id}/events")
    assert done["status"] == "complete"
    assert done["source"] == "curated"
    assert done["detail_mermaid"]
    assert len(done["components"]) >= 10
    assert calls["n"] == 0
    assert not any(lg["level"] == "error" for lg in logs)


# ── general repos ──────────────────────────────────────────────────────────────


async def test_small_fastapi_repo_produces_components_and_relationships(client):
    r = await client.post(
        "/api/scan/upload",
        files={"file": ("repo.zip", _make_zip(_FASTAPI_REPO), "application/zip")},
    )
    scan_id = r.json()["scan_id"]

    r2 = await client.post(f"/api/scans/{scan_id}/architecture")
    arch_id = r2.json()["architecture_id"]
    _, done = await _events(client, f"/api/architecture/{arch_id}/events")

    assert done["status"] == "complete"
    assert done["source"] == "static"
    assert "FastAPI" in done["technology_stack"]["frameworks"]
    assert len(done["components"]) >= 2
    assert len(done["relationships"]) >= 1
    assert done["mermaid"]
    assert any(ds["name"] == "SQLAlchemy" for ds in done["data_stores"])
    assert any(ep["kind"] == "http_server" for ep in done["entrypoints"])


async def test_model_unavailable_still_returns_complete_static_report(
    client, monkeypatch
):
    """Deterministic facts never depend on Granite/Bob in this build."""
    r = await client.post(
        "/api/scan/upload",
        files={"file": ("repo.zip", _make_zip(_FASTAPI_REPO), "application/zip")},
    )
    scan_id = r.json()["scan_id"]
    r2 = await client.post(f"/api/scans/{scan_id}/architecture")
    arch_id = r2.json()["architecture_id"]
    _, done = await _events(client, f"/api/architecture/{arch_id}/events")
    assert done["status"] == "complete"
    assert done["source"] == "static"


# ── lifecycle ───────────────────────────────────────────────────────────────────


async def test_unknown_scan_returns_404(client):
    r = await client.post("/api/scans/does-not-exist/architecture")
    assert r.status_code == 404
    assert "expired" in r.json()["detail"].lower()


async def test_repeated_request_reuses_the_same_architecture_id(client):
    r = await client.post("/api/scan", json={"source": "demo"})
    scan_id = r.json()["scan_id"]
    r1 = await client.post(f"/api/scans/{scan_id}/architecture")
    r2 = await client.post(f"/api/scans/{scan_id}/architecture")
    assert r1.json()["architecture_id"] == r2.json()["architecture_id"]


async def test_events_for_unknown_architecture_id_is_404(client):
    r = await client.get("/api/architecture/does-not-exist/events")
    assert r.status_code == 404


async def test_get_report_after_done_matches_stream(client):
    r = await client.post("/api/scan", json={"source": "demo"})
    scan_id = r.json()["scan_id"]
    r2 = await client.post(f"/api/scans/{scan_id}/architecture")
    arch_id = r2.json()["architecture_id"]
    _, done = await _events(client, f"/api/architecture/{arch_id}/events")

    r3 = await client.get(f"/api/architecture/{arch_id}")
    assert r3.status_code == 200
    assert r3.json()["status"] == done["status"]
    assert r3.json()["components"] == done["components"]


# ── download ────────────────────────────────────────────────────────────────────


async def test_download_mermaid_and_json(client):
    r = await client.post("/api/scan", json={"source": "demo"})
    scan_id = r.json()["scan_id"]
    r2 = await client.post(f"/api/scans/{scan_id}/architecture")
    arch_id = r2.json()["architecture_id"]
    _, done = await _events(client, f"/api/architecture/{arch_id}/events")

    r3 = await client.get(f"/api/architecture/{arch_id}/download?format=mermaid")
    assert r3.status_code == 200
    assert r3.text == done["mermaid"]
    assert r3.headers["content-type"].startswith("text/plain")

    r4 = await client.get(f"/api/architecture/{arch_id}/download?format=json")
    assert r4.status_code == 200
    assert r4.json()["architecture_id"] == arch_id


async def test_download_before_finished_is_409(client, monkeypatch):
    """Force the pipeline to still be 'running' when download is called, by
    making the curated load block briefly in its worker thread (it runs via
    asyncio.to_thread, so this never blocks the event loop itself)."""
    import time

    from app.architecture.curated import load_curated as real_load_curated

    def slow_load_curated(scan_id):
        time.sleep(0.3)
        return real_load_curated(scan_id)

    monkeypatch.setattr("app.pipelines.architecture.load_curated", slow_load_curated)

    r = await client.post("/api/scan", json={"source": "demo"})
    scan_id = r.json()["scan_id"]
    r2 = await client.post(f"/api/scans/{scan_id}/architecture")
    arch_id = r2.json()["architecture_id"]
    r3 = await client.get(f"/api/architecture/{arch_id}/download?format=mermaid")
    assert r3.status_code == 409


async def test_download_invalid_format_is_422(client):
    r = await client.post("/api/scan", json={"source": "demo"})
    scan_id = r.json()["scan_id"]
    r2 = await client.post(f"/api/scans/{scan_id}/architecture")
    arch_id = r2.json()["architecture_id"]
    await _events(client, f"/api/architecture/{arch_id}/events")
    r3 = await client.get(f"/api/architecture/{arch_id}/download?format=yaml")
    assert r3.status_code == 422


# ── timeout / partial ────────────────────────────────────────────────────────────


async def test_timeout_yields_error_or_partial_with_visible_log(client, monkeypatch):
    monkeypatch.setattr("app.config.ARCH_TIMEOUT_S", 0)
    r = await client.post(
        "/api/scan/upload",
        files={"file": ("repo.zip", _make_zip(_FASTAPI_REPO), "application/zip")},
    )
    scan_id = r.json()["scan_id"]
    r2 = await client.post(f"/api/scans/{scan_id}/architecture")
    arch_id = r2.json()["architecture_id"]
    logs, done = await _events(client, f"/api/architecture/{arch_id}/events")
    assert done["status"] in ("error", "partial")
    assert any(lg["level"] == "error" for lg in logs)


async def test_partial_status_when_parsed_file_budget_exceeded(client, monkeypatch):
    monkeypatch.setattr("app.architecture.inventory.ARCH_MAX_PARSED_FILES", 1)
    r = await client.post(
        "/api/scan/upload",
        files={"file": ("repo.zip", _make_zip(_FASTAPI_REPO), "application/zip")},
    )
    scan_id = r.json()["scan_id"]
    r2 = await client.post(f"/api/scans/{scan_id}/architecture")
    arch_id = r2.json()["architecture_id"]
    _, done = await _events(client, f"/api/architecture/{arch_id}/events")
    assert done["status"] == "partial"
    assert done["coverage"]["limit_exceeded"] == "ARCH_MAX_PARSED_FILES"
    assert done["narrowing_suggestions"]


async def test_huge_repo_returns_unavailable_not_a_bare_too_large_message(
    client, monkeypatch
):
    monkeypatch.setattr("app.architecture.inventory.ARCH_MAX_DISCOVERED_FILES", 2)
    files = {f"file_{i}.py": "x = 1\n" for i in range(10)}
    r = await client.post(
        "/api/scan/upload",
        files={"file": ("repo.zip", _make_zip(files), "application/zip")},
    )
    scan_id = r.json()["scan_id"]
    r2 = await client.post(f"/api/scans/{scan_id}/architecture")
    arch_id = r2.json()["architecture_id"]
    _, done = await _events(client, f"/api/architecture/{arch_id}/events")
    assert done["status"] == "unavailable"
    assert done["components"] == []
    assert done["coverage"]["files_discovered"] >= 2
    assert done["coverage"]["limit_exceeded"] == "ARCH_MAX_DISCOVERED_FILES"
    assert len(done["narrowing_suggestions"]) >= 2
    assert len(done["warnings"][0]) > len("Repository too large.")


# ── Robustness: hostile files and the event loop ──────────────────────────────


async def test_one_unparseable_file_does_not_sink_the_run(client):
    repo = dict(_FASTAPI_REPO)
    # Overflows CPython's parser stack (RecursionError), far below ARCH_MAX_FILE_BYTES.
    repo["app/generated.py"] = "x = a" + ".b" * 190_000 + "\n"
    r = await client.post(
        "/api/scan/upload",
        files={"file": ("repo.zip", _make_zip(repo), "application/zip")},
    )
    scan_id = r.json()["scan_id"]
    arch_id = (await client.post(f"/api/scans/{scan_id}/architecture")).json()[
        "architecture_id"
    ]
    logs, done = await _events(client, f"/api/architecture/{arch_id}/events")
    assert done["status"] in ("complete", "partial")
    assert len(done["components"]) >= 2
    assert not any(lg["level"] == "error" for lg in logs)


async def test_parsing_does_not_block_the_event_loop(client, monkeypatch):
    import asyncio
    import time

    from app.pipelines import architecture as pipeline

    real_extract = pipeline.extract_python

    def slow_extract(text, rel_path):
        time.sleep(0.1)  # stands in for a CPU-heavy parse
        return real_extract(text, rel_path)

    monkeypatch.setattr(pipeline, "extract_python", slow_extract)
    r = await client.post(
        "/api/scan/upload",
        files={"file": ("repo.zip", _make_zip(_FASTAPI_REPO), "application/zip")},
    )
    scan_id = r.json()["scan_id"]

    ticks = 0

    async def heartbeat():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1

    beat = asyncio.create_task(heartbeat())
    arch_id = (await client.post(f"/api/scans/{scan_id}/architecture")).json()[
        "architecture_id"
    ]
    _, done = await _events(client, f"/api/architecture/{arch_id}/events")
    beat.cancel()
    assert done["status"] == "complete"
    # 7 files x 0.1 s of parsing; a blocked loop would leave the heartbeat near 0.
    assert ticks >= 15, f"event loop was blocked during parsing (ticks={ticks})"
