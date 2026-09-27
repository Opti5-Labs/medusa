"""GET /api/scans/status: lets the UI drop scans the server no longer has."""

import io
import time
import zipfile

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client():
    from app.api import runs as runs_module
    from app.api import scan as scan_module
    from app.ratelimit import RateLimiter
    from app.store import RunStore

    store = RunStore()
    scan_module.set_store(store)
    runs_module.set_store(store)
    scan_module._scan_limiter = RateLimiter(max_calls=100, window_seconds=600)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    await store.stop()


async def _upload(client) -> str:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("calc.py", "def add(a, b):\n    return a - b\n")
    resp = await client.post(
        "/api/scan/upload", files={"file": ("r.zip", buf.getvalue(), "application/zip")}
    )
    return resp.json()["scan_id"]


async def test_reports_which_scans_still_exist(client):
    scan_id = await _upload(client)
    resp = await client.get(f"/api/scans/status?ids={scan_id},gone-1")
    body = resp.json()
    assert resp.status_code == 200
    assert body["alive"] == [scan_id]
    assert body["server_started_at"] <= time.time()


async def test_caps_the_number_of_ids(client):
    ids = ",".join(f"x{i}" for i in range(50))
    resp = await client.get(f"/api/scans/status?ids={ids}")
    assert resp.json()["alive"] == []


async def test_no_ids(client):
    assert (await client.get("/api/scans/status")).json()["alive"] == []
