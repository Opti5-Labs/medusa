"""
Tests for app/api/scan.py — contract round-trips, demo uniqueness,
rate limiting, unknown fields rejection, timeout → 504.

Uses httpx.AsyncClient with FastAPI's ASGI transport (no real server needed).
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=True),
        base_url="http://test",
    ) as c:
        # Manually trigger lifespan so the store is initialised
        from app.api import runs as runs_module
        from app.api import scan as scan_module
        from app.store import RunStore

        store = RunStore()
        scan_module.set_store(store)
        runs_module.set_store(store)
        store.start()
        yield c
        await store.stop()


# ── /api/health ───────────────────────────────────────────────────────────────


async def test_health(client):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


# ── Demo scan ─────────────────────────────────────────────────────────────────


async def test_demo_returns_scan_result(client):
    resp = await client.post("/api/scan", json={"source": "demo"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["repo_source"] == "demo"
    assert "scan_id" in data
    assert isinstance(data["issues"], list)
    assert isinstance(data["files_scanned"], list)
    assert isinstance(data["warnings"], list)


async def test_demo_unique_ids_each_call(client):
    r1 = await client.post("/api/scan", json={"source": "demo"})
    r2 = await client.post("/api/scan", json={"source": "demo"})
    assert r1.status_code == 200
    assert r2.status_code == 200
    d1, d2 = r1.json(), r2.json()
    # scan_ids must differ
    assert d1["scan_id"] != d2["scan_id"]
    # All issue ids must be different across both scans
    ids1 = {i["id"] for i in d1["issues"]}
    ids2 = {i["id"] for i in d2["issues"]}
    assert ids1.isdisjoint(ids2), "Issue IDs must be unique across scans"


@pytest.mark.parametrize(
    "repo_url",
    [
        "https://github.com/Ilakiancs/OptiLearn",
        "https://github.com/ChanithaAbey/OptiLearn-Test",
        "https://github.com/ilakiancs/optilearn/",  # case/trailing-slash insensitive
    ],
)
async def test_github_optilearn_mirror_runs_demo(client, repo_url):
    resp = await client.post(
        "/api/scan", json={"source": "github", "repo_url": repo_url}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["repo_source"] == "demo"
    assert isinstance(data["issues"], list)


# ── Request validation ────────────────────────────────────────────────────────


async def test_unknown_fields_rejected(client):
    resp = await client.post("/api/scan", json={"source": "demo", "evil_field": "x"})
    assert resp.status_code == 422


async def test_github_without_repo_url_rejected(client):
    resp = await client.post("/api/scan", json={"source": "github"})
    assert resp.status_code == 422


async def test_invalid_source_rejected(client):
    resp = await client.post("/api/scan", json={"source": "zip"})
    assert resp.status_code == 422


# ── Rate limiting ─────────────────────────────────────────────────────────────


async def test_rate_limit_demo_exempt(client):
    """Demo is exempt from the rate limit — 10 calls should all succeed."""
    for _ in range(10):
        resp = await client.post("/api/scan", json={"source": "demo"})
        assert resp.status_code == 200


async def test_rate_limit_upload_enforced(client, monkeypatch, tmp_path):
    """6th upload scan must be rate-limited (429); first 5 must not be 429."""
    import io
    import zipfile

    from app.ratelimit import RateLimiter

    # Use a fresh limiter with max_calls=5
    fresh = RateLimiter(max_calls=5, window_seconds=600)
    monkeypatch.setattr("app.api.scan._scan_limiter", fresh)

    # Build a minimal valid zip in memory
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("main.py", "x = 1")
    buf.seek(0)
    zip_bytes = buf.getvalue()  # get bytes without consuming

    rate_limited_at: int | None = None
    for i in range(6):
        resp = await client.post(
            "/api/scan/upload",
            files={"file": ("test.zip", zip_bytes, "application/zip")},
        )
        if resp.status_code == 429:
            rate_limited_at = i
            break
        # Accept 200 or 4xx (scan errors are fine — we only care about 429)
        assert resp.status_code != 429 or i >= 5

    assert rate_limited_at is not None, "Expected a 429 within 6 calls"
    assert rate_limited_at == 5, (
        f"Rate limit should trigger on call 6 (index 5), got index {rate_limited_at}"
    )


# ── ScanResult contract ───────────────────────────────────────────────────────


async def test_demo_contract_fields(client):
    resp = await client.post("/api/scan", json={"source": "demo"})
    assert resp.status_code == 200
    data = resp.json()
    required = {
        "scan_id",
        "repo_source",
        "language",
        "files_scanned",
        "files_total",
        "issues",
        "warnings",
    }
    assert required.issubset(data.keys())
    for issue in data["issues"]:
        issue_required = {"id", "title", "description", "priority", "source"}
        assert issue_required.issubset(issue.keys())
        assert issue["priority"] in ("Low", "Medium", "High")
        assert issue["source"] in ("scan", "github_issue")
