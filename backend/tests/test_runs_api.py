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
from app.models.contracts import Issue
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
    assert any(
        "Granite: Granite is not configured" in w and "Bob: BOB_API_KEY is not set" in w
        for w in scan["warnings"]
    )


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


# ── Bob as an investigator ────────────────────────────────────────────────────


def _bob_says(monkeypatch, *, fix_index: int | None, confidence: float = 0.6):
    """Make live Bob 'available' and answer with a prepared function as its fix."""
    from app.agents import panel
    from app.agents.results import CandidateFix, InvestigatorResult

    fixes = []
    if fix_index is not None:
        fixes = [
            CandidateFix(
                approach="Bob's fix",
                function_source=optilearn.load_prepared()[fix_index].source,
            )
        ]

    async def fake_investigate(prompt, files):
        return InvestigatorResult(
            investigator="bob",
            status="ok",
            root_cause="Bob: the raw path is returned when no folder exists",
            confidence=confidence,
            candidate_fixes=fixes,
            cost=0.11,
        )

    monkeypatch.setattr(panel.bob, "live_unavailable_reason", lambda: None)
    monkeypatch.setattr(panel.bob, "investigate", fake_investigate)


async def test_bob_available_granite_unavailable(client, fake_sandbox, monkeypatch):
    _bob_says(monkeypatch, fix_index=0)
    issue_id = await _demo_issue(client)

    attempt = (await client.post(f"/api/issues/{issue_id}/repro")).json()
    logs, done = await _events(client, f"/api/repro/{attempt['attempt_id']}/events")
    assert done["status"] == "reproduced"
    assert done["investigator_source"] == "bob"
    reports = {r["investigator"]: r for r in done["investigators"]}
    assert reports["bob"]["status"] == "ok" and reports["bob"]["cost"] == 0.11
    assert reports["granite"]["status"] == "unavailable"
    assert "not configured" in reports["granite"]["error"]
    assert done["root_cause"].startswith("Bob:")

    session = (
        await client.post(f"/api/issues/{issue_id}/debug", json={"candidates": 4})
    ).json()
    logs, done = await _events(client, f"/api/debug/{session['session_id']}/events")
    cands = done["session"]["candidates"]
    assert cands[0]["origin"] == "bob" and cands[0]["sandbox_status"] == "passed"
    assert [c["origin"] for c in cands[1:]] == ["prepared"] * 3
    assert any("neither Bob nor Granite" in e["message"] for e in logs)
    assert done["recommendation"]["verified"] is True


async def test_bob_confidence_never_picks_the_winner(client, fake_sandbox, monkeypatch):
    # Bob is very confident in a fix that breaks an existing behaviour check.
    _bob_says(monkeypatch, fix_index=2, confidence=0.99)
    issue_id = await _demo_issue(client)
    session = (
        await client.post(f"/api/issues/{issue_id}/debug", json={"candidates": 2})
    ).json()
    _, done = await _events(client, f"/api/debug/{session['session_id']}/events")
    bob_cand = done["session"]["candidates"][0]
    assert bob_cand["origin"] == "bob" and bob_cand["sandbox_status"] == "failed"
    assert bob_cand["test_results"]["regressions"]
    assert done["recommendation"]["candidate_id"] == "c2"  # the sandbox-verified one


async def test_both_unavailable_uses_labelled_prepared_candidates(client, fake_sandbox):
    issue_id = await _demo_issue(client)
    attempt = (await client.post(f"/api/issues/{issue_id}/repro")).json()
    logs, done = await _events(client, f"/api/repro/{attempt['attempt_id']}/events")
    assert done["investigator_source"] == "unavailable"
    messages = [e["message"] for e in logs]
    assert any("Bob unavailable: BOB_API_KEY is not set" in m for m in messages)
    assert any(m.startswith("Granite unavailable") for m in messages)

    session = (
        await client.post(f"/api/issues/{issue_id}/debug", json={"candidates": 2})
    ).json()
    logs, done = await _events(client, f"/api/debug/{session['session_id']}/events")
    assert all(c["origin"] == "prepared" for c in done["session"]["candidates"])
    assert any(
        "Bob has no fixes to offer: BOB_API_KEY is not set" in e["message"]
        for e in logs
    )


async def test_bob_key_never_reaches_the_client(
    client, fake_sandbox, monkeypatch, tmp_path
):
    """The key is never in any API response or event, even when Bob echoes it in an error."""
    secret = "bob-secret-key-DO-NOT-LEAK-0123456789"
    script = tmp_path / "fake-bob"
    script.write_text(
        f"#!{__import__('sys').executable}\nimport os, sys\n"
        "sys.stderr.write('Error: Request Failed. Invalid or expired API key ' + os.environ['BOB_API_KEY'])\n"
        "sys.exit(1)\n"
    )
    script.chmod(0o755)
    monkeypatch.setattr("app.config.BOB_API_KEY", secret)
    monkeypatch.setattr("app.config.BOB_BINARY", str(script))

    scan = await client.post("/api/scan", json={"source": "demo"})
    issue_id = scan.json()["issues"][0]["id"]
    attempt = await client.post(f"/api/issues/{issue_id}/repro")
    events = await client.get(f"/api/repro/{attempt.json()['attempt_id']}/events")
    session = await client.post(f"/api/issues/{issue_id}/debug", json={"candidates": 2})
    debug_events = await client.get(f"/api/debug/{session.json()['session_id']}/events")

    everything = "".join(r.text for r in (scan, attempt, events, session, debug_events))
    assert "Bob authentication failed" in everything  # the real reason is surfaced...
    assert secret not in everything  # ...without the key


def test_frontend_never_references_the_bob_key():
    from pathlib import Path

    frontend = Path(__file__).resolve().parents[2] / "frontend"
    offenders = [
        str(p.relative_to(frontend))
        for p in list(frontend.glob("app/**/*"))
        + list(frontend.glob("lib/**/*"))
        + list(frontend.glob("*.*"))
        if p.is_file()
        and p.suffix in {".ts", ".tsx", ".js", ".mjs", ".json", ".example"}
        and (
            "BOB_API_KEY" in p.read_text("utf-8", "ignore")
            or "NEXT_PUBLIC_BOB" in p.read_text("utf-8", "ignore")
        )
    ]
    assert offenders == []


async def test_general_repo_bob_diagnoses_and_proposes_unverified_patch(
    client, monkeypatch
):
    from app.agents import panel
    from app.agents.results import CandidateFix, InvestigatorResult

    async def fake_investigate(prompt, files):
        assert "nothing can be executed" in prompt
        return InvestigatorResult(
            investigator="bob",
            status="ok",
            root_cause="divide() does not guard b == 0",
            confidence=0.7,
            candidate_fixes=[
                CandidateFix(
                    approach="Guard zero",
                    patch="--- a/app/main.py\n+++ b/app/main.py\n@@\n-x\n+y\n",
                )
            ],
        )

    monkeypatch.setattr(panel.bob, "live_unavailable_reason", lambda: None)
    monkeypatch.setattr(panel.bob, "investigate", fake_investigate)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("app/main.py", "def divide(a, b):\n    return a / b\n")
    scan = (
        await client.post(
            "/api/scan/upload",
            files={"file": ("r.zip", buf.getvalue(), "application/zip")},
        )
    ).json()
    # A GitHub-style issue pointing at the file (scan issues need Granite).
    from app.api import runs as runs_module

    record = await runs_module._get_store().get(scan["scan_id"])
    issue = Issue(
        id="gen-1",
        title="Division by zero in divide",
        description="divide crashes",
        priority="High",
        source="github_issue",
        file="app/main.py",
    )
    record.result.issues.append(issue)
    runs_module._get_store()._issue_index[issue.id] = scan["scan_id"]

    attempt = (await client.post("/api/issues/gen-1/repro")).json()
    _, done = await _events(client, f"/api/repro/{attempt['attempt_id']}/events")
    assert done["mode"] == "reasoning" and done["status"] == "plausible"
    assert done["confidence"] == 0.7 and done["investigator_source"] == "bob"

    session = (await client.post("/api/issues/gen-1/debug", json={})).json()
    _, done = await _events(client, f"/api/debug/{session['session_id']}/events")
    first = done["session"]["candidates"][0]
    assert first["origin"] == "bob" and first["sandbox_status"] == "not_applicable"
    assert done["recommendation"]["verified"] is False
