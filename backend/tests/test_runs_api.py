"""
End-to-end API tests for reproduce and debug, including the SSE streams.

The sandbox is replaced by a fake runner that scores a patched tree from its
source text (the real container is covered in test_sandbox_runner.py), so these
run anywhere. Granite is unconfigured, so the demo uses prepared candidates.
"""

import io
import json
import zipfile

import pytest
from httpx import ASGITransport, AsyncClient

from app.demo import optilearn
from app.main import app
from app.sandbox.runner import GroupResult, SandboxResult


def _fake_result(target_text: str) -> SandboxResult:
    body = target_text.split("def _resolve_hf_asr_model", 1)[1].split("\ndef ", 1)[0]
    repro_ok, suite_failures = True, []
    if "openai/whisper-tiny" not in body or "removeprefix" in body:
        repro_ok = False  # original code, or a candidate that still returns a path
    elif body.rstrip().endswith('return "openai/whisper-tiny"'):
        suite_failures = ["test_behaviour.py::test_custom_hub_id_is_not_overridden"]
    return SandboxResult(
        ok=True,
        reproducer=GroupResult(
            int(repro_ok),
            int(not repro_ok),
            1,
            [] if repro_ok else ["test_reproducer.py::test_missing"],
        ),
        suite=GroupResult(
            6 - len(suite_failures), len(suite_failures), 6, suite_failures
        ),
    )


@pytest.fixture
def fake_sandbox(monkeypatch):
    async def fake_run_checks(code_dir, on_line):
        await on_line("Running reproducer, behaviour checks and OptiLearn test suite")
        return _fake_result((code_dir / optilearn.TARGET_FILE).read_text())

    for mod in ("app.pipelines.repro", "app.pipelines.debug"):
        monkeypatch.setattr(f"{mod}.run_checks", fake_run_checks)
        monkeypatch.setattr(f"{mod}.docker_available", lambda: True)


@pytest.fixture
async def client():
    from app.api import runs as runs_module
    from app.api import scan as scan_module
    from app.ratelimit import RateLimiter
    from app.store import RunStore

    store = RunStore()
    scan_module.set_store(store)
    runs_module.set_store(store)
    runs_module._run_limiter = RateLimiter(max_calls=100, window_seconds=600)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    await store.stop()


async def _events(client: AsyncClient, url: str) -> tuple[list[dict], dict]:
    """Read an SSE stream to the end; return (log events, done payload)."""
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


async def _demo_issue(client: AsyncClient) -> str:
    resp = await client.post("/api/scan", json={"source": "demo"})
    return resp.json()["issues"][0]["id"]


async def test_demo_repro_reproduces_in_sandbox(client, fake_sandbox):
    issue_id = await _demo_issue(client)
    resp = await client.post(f"/api/issues/{issue_id}/repro")
    assert resp.status_code == 200
    attempt = resp.json()
    assert attempt["mode"] == "sandboxed" and attempt["status"] == "running"

    logs, done = await _events(client, f"/api/repro/{attempt['attempt_id']}/events")
    assert done["status"] == "reproduced"
    assert done["investigator_source"] == "unavailable"  # no Granite, no golden run
    assert any(e["source"] == "sandbox" and e["level"] == "result" for e in logs)
    assert done["confidence"] is None  # never on sandboxed results


async def test_demo_debug_race_verifies_and_recommends(client, fake_sandbox):
    issue_id = await _demo_issue(client)
    resp = await client.post(f"/api/issues/{issue_id}/debug", json={"candidates": 4})
    session = resp.json()
    assert len(session["candidates"]) == 4

    logs, done = await _events(client, f"/api/debug/{session['session_id']}/events")
    statuses = {
        c["candidate_id"]: c["sandbox_status"] for c in done["session"]["candidates"]
    }
    assert statuses == {"c1": "passed", "c2": "passed", "c3": "failed", "c4": "failed"}
    assert all(c["origin"] == "prepared" for c in done["session"]["candidates"])
    c3 = done["session"]["candidates"][2]
    assert c3["test_results"]["regressions"] == [
        "test_behaviour.py::test_custom_hub_id_is_not_overridden"
    ]

    rec = done["recommendation"]
    assert rec["candidate_id"] == "c1"  # smallest passing patch
    assert rec["verified"] is True
    assert any(e["source"] == "gate" for e in logs)
    assert any(e["source"] == "candidate:c2" for e in logs)

    dl = await client.get(
        f"/api/debug/{session['session_id']}/download", params={"candidate_id": "c1"}
    )
    assert dl.status_code == 200
    assert dl.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(dl.content)) as zf:
        assert (
            "openai/whisper-tiny"
            in zf.read(f"optilearn/{optilearn.TARGET_FILE}").decode()
        )

    refused = await client.get(
        f"/api/debug/{session['session_id']}/download", params={"candidate_id": "c3"}
    )
    assert refused.status_code == 409


async def test_debug_reuses_reproduce_baseline(client, fake_sandbox):
    issue_id = await _demo_issue(client)
    attempt = (await client.post(f"/api/issues/{issue_id}/repro")).json()
    await _events(client, f"/api/repro/{attempt['attempt_id']}/events")
    session = (
        await client.post(f"/api/issues/{issue_id}/debug", json={"candidates": 2})
    ).json()
    logs, _ = await _events(client, f"/api/debug/{session['session_id']}/events")
    assert any("reusing the reproduction" in e["message"] for e in logs)


async def test_bug_gate_closed_when_not_reproducible(client, monkeypatch):
    async def always_passes(code_dir, on_line):
        return SandboxResult(
            ok=True, reproducer=GroupResult(1, 0, 1, []), suite=GroupResult(6, 0, 6, [])
        )

    for mod in ("app.pipelines.repro", "app.pipelines.debug"):
        monkeypatch.setattr(f"{mod}.run_checks", always_passes)
        monkeypatch.setattr(f"{mod}.docker_available", lambda: True)
    issue_id = await _demo_issue(client)
    session = (
        await client.post(f"/api/issues/{issue_id}/debug", json={"candidates": 2})
    ).json()
    logs, done = await _events(client, f"/api/debug/{session['session_id']}/events")
    assert done["recommendation"] is None
    assert all(c["sandbox_status"] == "failed" for c in done["session"]["candidates"])
    assert any(e["level"] == "error" and "gate closed" in e["message"] for e in logs)


async def test_sandbox_unavailable_is_a_visible_error(client, monkeypatch):
    monkeypatch.setattr("app.pipelines.repro.docker_available", lambda: False)
    issue_id = await _demo_issue(client)
    attempt = (await client.post(f"/api/issues/{issue_id}/repro")).json()
    logs, done = await _events(client, f"/api/repro/{attempt['attempt_id']}/events")
    assert done["status"] == "error"
    assert any(e["level"] == "error" for e in logs)


async def test_general_repo_repro_without_granite_errors_visibly(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("app/main.py", "def divide(a, b):\n    return a / b\n")
    scan = (
        await client.post(
            "/api/scan/upload",
            files={"file": ("r.zip", buf.getvalue(), "application/zip")},
        )
    ).json()
    assert scan["issues"] == []  # nothing fabricated without Granite
    assert any("Granite is not configured" in w for w in scan["warnings"])


async def test_debug_candidate_bounds(client, fake_sandbox):
    issue_id = await _demo_issue(client)
    for bad in (1, 7):
        resp = await client.post(
            f"/api/issues/{issue_id}/debug", json={"candidates": bad}
        )
        assert resp.status_code == 422


async def test_unknown_issue_is_404(client):
    resp = await client.post("/api/issues/does-not-exist/repro")
    assert resp.status_code == 404
    assert "scan" in resp.json()["detail"].lower()


async def test_runs_are_rate_limited(client, fake_sandbox):
    from app.api import runs as runs_module
    from app.ratelimit import RateLimiter

    runs_module._run_limiter = RateLimiter(
        max_calls=1, window_seconds=600, what="reproduce or debug runs"
    )
    issue_id = await _demo_issue(client)
    assert (await client.post(f"/api/issues/{issue_id}/repro")).status_code == 200
    resp = await client.post(f"/api/issues/{issue_id}/repro")
    assert resp.status_code == 429
    assert "reproduce or debug runs" in resp.json()["detail"]


async def test_failed_granite_candidate_is_revised_with_test_feedback(
    client, fake_sandbox, monkeypatch
):
    from app.agents.fixers import FunctionCandidate

    prepared = optilearn.load_prepared()
    minimal_guard, always_default = prepared[0].source, prepared[2].source
    calls: list[dict] = []

    async def fake_candidate(
        issue,
        root_cause,
        name,
        current,
        context,
        strategy,
        acceptance,
        previous=None,
        feedback=None,
    ):
        calls.append({"previous": previous, "feedback": feedback})
        # First attempt over-corrects; the revision gets it right.
        return FunctionCandidate(
            approach=strategy,
            function_source=minimal_guard if previous else always_default,
        )

    monkeypatch.setattr("app.pipelines.debug.is_configured", lambda: True)
    monkeypatch.setattr("app.pipelines.debug.function_candidate", fake_candidate)
    issue_id = await _demo_issue(client)
    session = (
        await client.post(f"/api/issues/{issue_id}/debug", json={"candidates": 2})
    ).json()
    logs, done = await _events(client, f"/api/debug/{session['session_id']}/events")

    for c in done["session"]["candidates"]:
        assert (c["origin"], c["attempts"], c["sandbox_status"]) == (
            "granite",
            2,
            "passed",
        )
    revisions = [c for c in calls if c["previous"]]
    assert len(revisions) == 2
    assert "test_custom_hub_id_is_not_overridden" in revisions[0]["feedback"]
    assert any("revising with the test output" in e["message"] for e in logs)
    assert done["recommendation"]["verified"] is True
