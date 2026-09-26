"""
Live reproduction for general repos (slice 2): eligibility, reproducer drafts,
and the reproduce pipeline's sandbox step. The sandbox and models are stubbed;
real containers are covered by tests/test_pyexec.py.
"""

import io
import zipfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.agents import reproducer
from app.agents.results import InvestigatorResult
from app.main import app
from app.models.contracts import Issue, ScanResult
from app.pipelines.execution import execution_ineligible_reason
from app.sandbox import pyexec
from app.store import ScanRecord

# ── Eligibility ───────────────────────────────────────────────────────────────


def _record(tmp_path: Path, files: dict[str, str], source: str = "zip") -> ScanRecord:
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    result = ScanResult(
        scan_id="s",
        repo_source=source,
        language="Python",
        files_scanned=[],
        files_total=0,
        issues=[],
    )
    return ScanRecord(scan_id="s", tmp_dir=None, result=result, root=tmp_path)


def test_python_project_with_tests_is_eligible(tmp_path):
    record = _record(
        tmp_path, {"calc.py": "x = 1\n", "tests/test_calc.py": "def test_x(): pass\n"}
    )
    assert execution_ineligible_reason(record) is None


@pytest.mark.parametrize(
    ("files", "reason"),
    [
        ({"index.js": "1"}, "not a Python project"),
        ({"calc.py": "x = 1\n"}, "no Python tests"),
    ],
)
def test_ineligible_repos_say_why(tmp_path, files, reason):
    assert reason in execution_ineligible_reason(_record(tmp_path, files))


def test_demo_is_never_run_this_way(tmp_path):
    record = _record(tmp_path, {"t/test_a.py": "def test_a(): pass\n"}, source="demo")
    assert "only linked repositories" in execution_ineligible_reason(record)


# ── Reproducer drafts ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("source", "problem"),
    [
        ("", "empty"),
        ("def test_x(:\n", "not valid Python"),
        ("x = 1\n", "no test function"),
        ("def test_x():\n    assert 1\n" * 2000, "too long"),
    ],
)
def test_validate_rejects_unusable_tests(source, problem):
    assert problem in reproducer.validate(source)


def test_validate_accepts_a_test():
    assert reproducer.validate("def test_bug():\n    assert f() == 1\n") is None


ISSUE = Issue(
    id="i",
    title="add subtracts",
    description="add(2, 3) returns -1",
    priority="High",
    source="scan",
)


async def test_bob_writes_the_reproducer_when_granite_is_unavailable(monkeypatch):
    from app.agents import bob

    async def fake_ask(prompt, files, schema, timeout_s=None, text_field=None):
        assert "must FAIL on the current code" in prompt
        return bob.BobAnswer(
            "ok",
            data=schema(test_source="def test_add():\n    assert add(2, 3) == 5\n"),
        )

    monkeypatch.setattr(reproducer.bob, "ask", fake_ask)
    draft = await reproducer.write_reproducer(ISSUE, {"calc.py": "..."}, "subtracts")
    assert draft.author == "bob" and draft.source.startswith("def test_add")


async def test_unusable_drafts_are_reported(monkeypatch):
    from app.agents import bob

    async def fake_ask(prompt, files, schema, timeout_s=None, text_field=None):
        return bob.BobAnswer("ok", data=schema(test_source="x = 1\n"))

    monkeypatch.setattr(reproducer.bob, "ask", fake_ask)
    draft = await reproducer.write_reproducer(ISSUE, {}, None)
    assert draft.source is None and "no test function" in draft.error


# ── The reproduce pipeline's sandbox step (through the API) ───────────────────

_REPO = {
    "calc/__init__.py": "def add(a, b):\n    return a - b\n",
    "tests/test_calc.py": "from calc import add\n\ndef test_zero():\n    assert add(0, 0) == 0\n",
}
_GOOD_TEST = "from calc import add\n\ndef test_add_adds():\n    assert add(2, 3) == 5\n"


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
    scan_module._scan_limiter = RateLimiter(max_calls=100, window_seconds=600)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    await store.stop()


async def _events(client, url):
    import json

    resp = await client.get(url)
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
    return logs, done


async def _issue_in_uploaded_repo(client, files=_REPO, file="calc/__init__.py") -> str:
    from app.api import runs as runs_module

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for rel, text in files.items():
            zf.writestr(rel, text)
    scan = (
        await client.post(
            "/api/scan/upload",
            files={"file": ("r.zip", buf.getvalue(), "application/zip")},
        )
    ).json()
    store = runs_module._get_store()
    record = await store.get(scan["scan_id"])
    issue = Issue(
        id="gen-x",
        title="add subtracts",
        description="add(2, 3) gives -1",
        priority="High",
        source="github_issue",
        file=file,
    )
    record.result.issues.append(issue)
    store._issue_index[issue.id] = scan["scan_id"]
    return issue.id


@pytest.fixture
def sandbox(monkeypatch):
    """A stub sandbox whose test outcomes are scripted per call."""
    state = {"outcomes": [], "released": 0, "prepare_error": None, "tests_seen": []}
    from app.pipelines import repro

    async def fake_investigate(prompt, files):
        return InvestigatorResult(
            investigator="bob", status="ok", root_cause="add subtracts", confidence=0.7
        )

    async def prepare(code_dir, on_line):
        if state["prepare_error"]:
            return pyexec.PrepareResult(None, state["prepare_error"])
        return pyexec.PrepareResult(pyexec.ExecEnv(volume="v", code_dir=code_dir))

    async def run_tests(env, on_line, *, patch=None, repro_test=None, select=None):
        state["tests_seen"].append(repro_test)
        outcome = state["outcomes"].pop(0)
        if outcome == "error":
            await on_line(
                "COLLECT-ERROR tests/test_medusa_repro.py: E   ImportError: no module named calcs"
            )
        return pyexec.TestRun(
            ok=True, repro_outcome=outcome, suite=pyexec.Group(passed=1, total=1)
        )

    async def release(env):
        state["released"] += 1

    monkeypatch.setattr(repro.panel.bob, "live_unavailable_reason", lambda: None)
    monkeypatch.setattr(repro.panel.bob, "investigate", fake_investigate)
    monkeypatch.setattr(repro.pyexec, "unavailable_reason", lambda: None)
    monkeypatch.setattr(repro.pyexec, "prepare", prepare)
    monkeypatch.setattr(repro.pyexec, "run_tests", run_tests)
    monkeypatch.setattr(repro.pyexec, "release", release)
    return state


@pytest.fixture
def drafts(monkeypatch):
    calls: list[dict] = []
    from app.pipelines import repro

    async def fake_write(issue, files, root_cause, *, previous=None, feedback=None):
        calls.append({"previous": previous, "feedback": feedback})
        return reproducer.Draft(_GOOD_TEST, "bob")

    monkeypatch.setattr(repro, "write_reproducer", fake_write)
    return calls


async def _repro(client, issue_id):
    attempt = (await client.post(f"/api/issues/{issue_id}/repro")).json()
    return await _events(client, f"/api/repro/{attempt['attempt_id']}/events")


async def test_failing_reproducer_confirms_the_bug(client, sandbox, drafts):
    sandbox["outcomes"] = ["failed"]
    logs, done = await _repro(client, await _issue_in_uploaded_repo(client))
    assert (done["mode"], done["status"]) == ("sandboxed", "reproduced")
    assert done["reproducer_test"] == _GOOD_TEST
    assert done["confidence"] is None  # confirmed by a test, not estimated
    assert sandbox["released"] == 1
    assert any("Reproduced: the reproducer test fails" in e["message"] for e in logs)


async def test_an_erroring_test_is_revised_with_the_sandbox_output(
    client, sandbox, drafts
):
    sandbox["outcomes"] = ["error", "failed"]
    _, done = await _repro(client, await _issue_in_uploaded_repo(client))
    assert done["status"] == "reproduced"
    assert drafts[1]["previous"] == _GOOD_TEST
    assert "ImportError" in drafts[1]["feedback"]


async def test_a_passing_test_never_claims_not_reproducible(client, sandbox, drafts):
    sandbox["outcomes"] = ["passed", "passed"]
    logs, done = await _repro(client, await _issue_in_uploaded_repo(client))
    assert (done["mode"], done["status"]) == ("reasoning", "plausible")
    assert done["reproducer_test"] is None
    assert "PASSED on the current code" in drafts[1]["feedback"]
    assert any("stays an analysis (plausible)" in e["message"] for e in logs)
    assert sandbox["released"] == 1


async def test_sandbox_failure_falls_back_to_the_analysis(client, sandbox, drafts):
    sandbox["prepare_error"] = (
        "Installing dependencies failed: stopped after the 300 s time limit."
    )
    logs, done = await _repro(client, await _issue_in_uploaded_repo(client))
    assert done["status"] == "plausible"
    assert any(
        "Could not run the repository: Installing dependencies failed" in e["message"]
        for e in logs
    )
    assert drafts == []


async def test_non_python_repo_is_not_run(client, sandbox, drafts):
    files = {"calc.js": "module.exports = (a, b) => a - b\n"}
    logs, done = await _repro(
        client, await _issue_in_uploaded_repo(client, files, file="calc.js")
    )
    assert done["status"] == "plausible"
    assert any(
        "Not running this repository's code: it is not a Python project" in e["message"]
        for e in logs
    )
    assert sandbox["tests_seen"] == []


async def test_execution_off_keeps_todays_behaviour(client, monkeypatch, drafts):
    from app.pipelines import repro

    async def fake_investigate(prompt, files):
        return InvestigatorResult(
            investigator="bob", status="ok", root_cause="add subtracts", confidence=0.7
        )

    monkeypatch.setattr(repro.panel.bob, "live_unavailable_reason", lambda: None)
    monkeypatch.setattr(repro.panel.bob, "investigate", fake_investigate)
    logs, done = await _repro(client, await _issue_in_uploaded_repo(client))
    assert done["status"] == "plausible"
    assert any("turned off on this server" in e["message"] for e in logs)
    assert drafts == []
