"""Tests for the Granite scan pipeline: chunking, result mapping, failure handling."""

import asyncio

import pytest

from app.agents.granite import GraniteError
from app.pipelines import scan as scan_mod


def _write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_long_python_file_splits_at_definitions(tmp_path):
    text = "\n".join(f"def f{i}():\n" + "    x = 1\n" * 48 for i in range(20))
    segments = scan_mod.split_file("big.py", text, 400)
    assert len(segments) > 1
    assert all(len(s.lines) <= 400 for s in segments)
    assert all(s.lines[0].startswith("def ") for s in segments)
    assert sum(len(s.lines) for s in segments) == len(text.splitlines())


def test_small_files_are_packed_together(tmp_path):
    files = [_write(tmp_path, f"m{i}.py", "x = 1\n" * 50) for i in range(5)]
    chunks = scan_mod.build_chunks(tmp_path, files, 400)
    assert len(chunks) == 1 and len(chunks[0].segments) == 5


def test_total_lines_are_capped(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.SCAN_MAX_LINES", 100)
    files = [_write(tmp_path, "huge.py", "x = 1\n" * 5000)]
    chunks = scan_mod.build_chunks(tmp_path, files, 400)
    assert sum(c.size for c in chunks) == 100


def _review(*findings):
    return scan_mod._ChunkReview(issues=[scan_mod._Finding(**f) for f in findings])


async def test_findings_map_to_issues_and_bad_paths_are_dropped(tmp_path, monkeypatch):
    f = _write(tmp_path, "app/db.py", "def q(s):\n    return 'SELECT ' + s\n")

    async def fake_chat(system, user, schema, **kw):
        return _review(
            {
                "file": "app/db.py",
                "line": 2,
                "function": "q",
                "title": "SQL injection",
                "description": "User input concatenated into SQL.",
                "priority": "high",
                "category": "Security",
            },
            {"file": "../../etc/passwd", "line": 1, "title": "x", "description": "y"},
            {
                "file": "app/db.py",
                "line": 999,
                "title": "Out of range line",
                "description": "z",
                "priority": "urgent",
                "category": "style",
            },
        )

    monkeypatch.setattr(scan_mod.granite, "chat_json", fake_chat)
    loop_time = asyncio.get_running_loop().time()
    issues, warnings = await scan_mod.analyze(tmp_path, [f], loop_time + 30)
    assert warnings == []
    assert [i.title for i in issues] == ["SQL injection", "Out of range line"]
    first, second = issues
    assert (first.priority, first.category, first.file, first.line) == (
        "High",
        "security",
        "app/db.py",
        2,
    )
    assert (second.priority, second.category, second.line) == ("Medium", None, None)


async def test_failed_chunk_is_skipped_with_warning(tmp_path, monkeypatch):
    files = [_write(tmp_path, f"m{i}.py", "x = 1\n" * 300) for i in range(2)]
    calls = 0

    async def flaky(system, user, schema, **kw):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise GraniteError("Granite call failed: the model returned invalid JSON.")
        return _review()

    monkeypatch.setattr(scan_mod.granite, "chat_json", flaky)
    issues, warnings = await scan_mod.analyze(
        tmp_path, files, asyncio.get_running_loop().time() + 30
    )
    assert issues == []
    assert len(warnings) == 1 and "1 of 2 code chunks" in warnings[0]


async def test_slow_chunks_are_skipped_at_deadline(tmp_path, monkeypatch):
    files = [_write(tmp_path, "m.py", "x = 1\n")]

    async def slow(*a, **kw):
        await asyncio.sleep(10)

    monkeypatch.setattr(scan_mod.granite, "chat_json", slow)
    issues, warnings = await scan_mod.analyze(
        tmp_path, files, asyncio.get_running_loop().time()
    )
    assert issues == []
    assert "time limit" in warnings[0]


@pytest.mark.parametrize("configured", [False, True])
async def test_scan_repo_labels_missing_granite(tmp_path, monkeypatch, configured):
    _write(tmp_path, "main.py", "print(1)\n")
    if configured:
        monkeypatch.setattr(scan_mod.granite, "is_configured", lambda: True)

        async def none_found(*a, **kw):
            return _review()

        monkeypatch.setattr(scan_mod.granite, "chat_json", none_found)
    result = await scan_mod.scan_repo(tmp_path, "zip")
    assert result.files_scanned == ["main.py"] and result.files_total == 1
    assert any("not configured" in w for w in result.warnings) is (not configured)


# ── Bob scan when Granite cannot analyse the code ─────────────────────────────


def _bob_answers(monkeypatch, issues, *, status="ok", error=None, calls=None):
    from app.agents import bob

    async def fake_ask(prompt, files, schema, timeout_s=None):
        if calls is not None:
            calls.append({"prompt": prompt, "files": files})
        if status != "ok":
            return bob.BobAnswer(status, error=error)
        return bob.BobAnswer(
            "ok", data=schema.model_validate({"issues": issues}), cost=0.07
        )

    monkeypatch.setattr(scan_mod.bob, "ask", fake_ask)


async def test_bob_scans_when_granite_is_not_configured(tmp_path, monkeypatch):
    _write(tmp_path, "app/db.py", "def q(s):\n    return 'SELECT ' + s\n")
    calls: list = []
    _bob_answers(
        monkeypatch,
        [
            {
                "file": "app/db.py",
                "line": 2,
                "title": "SQL injection",
                "description": "Concatenated SQL.",
                "priority": "high",
                "category": "security",
            },
            {"file": "../etc/passwd", "line": 1, "title": "x", "description": "y"},
        ],
        calls=calls,
    )
    result = await scan_mod.scan_repo(tmp_path, "zip")
    assert [i.title for i in result.issues] == ["SQL injection"]  # unshown file dropped
    issue = result.issues[0]
    assert (issue.found_by, issue.priority, issue.category, issue.line) == (
        "bob",
        "High",
        "security",
        2,
    )
    assert any(
        "Code analysed by IBM Bob because Granite is unavailable" in w
        and "0.07 Bobcoins" in w
        for w in result.warnings
    )
    assert "nothing can be executed" in calls[0]["prompt"]
    assert calls[0]["files"] == {"app/db.py": "def q(s):\n    return 'SELECT ' + s\n"}


async def test_bob_scans_when_every_granite_chunk_hits_the_quota(tmp_path, monkeypatch):
    from app.agents.granite import GraniteUnavailable

    _write(tmp_path, "m.py", "x = 1\n")

    async def quota(*a, **kw):
        raise GraniteUnavailable(
            "Granite call failed: the watsonx.ai token quota for this project is used up."
        )

    monkeypatch.setattr(scan_mod.granite, "is_configured", lambda: True)
    monkeypatch.setattr(scan_mod.granite, "chat_json", quota)
    _bob_answers(
        monkeypatch,
        [{"file": "m.py", "line": 1, "title": "Unused global", "description": "d"}],
    )
    result = await scan_mod.scan_repo(tmp_path, "zip")
    assert [i.found_by for i in result.issues] == ["bob"]
    assert any("token quota" in w and "IBM Bob" in w for w in result.warnings)


async def test_bob_does_not_scan_when_granite_works(tmp_path, monkeypatch):
    _write(tmp_path, "m.py", "x = 1\n")
    calls: list = []
    _bob_answers(monkeypatch, [], calls=calls)
    monkeypatch.setattr(scan_mod.granite, "is_configured", lambda: True)

    async def ok(*a, **kw):
        return _review({"file": "m.py", "line": 1, "title": "G", "description": "d"})

    monkeypatch.setattr(scan_mod.granite, "chat_json", ok)
    result = await scan_mod.scan_repo(tmp_path, "zip")
    assert calls == []
    assert [i.found_by for i in result.issues] == ["granite"]


async def test_both_unavailable_names_both_reasons(tmp_path, monkeypatch):
    _write(tmp_path, "m.py", "x = 1\n")
    _bob_answers(
        monkeypatch,
        [],
        status="error",
        error="Bob authentication failed: Invalid or expired API key.",
    )
    result = await scan_mod.scan_repo(tmp_path, "zip")
    assert result.issues == []
    warning = next(
        w for w in result.warnings if w.startswith("Code analysis is unavailable")
    )
    assert "Granite: Granite is not configured" in warning
    assert "Bob: Bob authentication failed" in warning


def test_bob_scan_respects_the_file_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(scan_mod, "_BOB_SCAN_MAX_CHARS", 100)
    files = [_write(tmp_path, f"f{i}.py", "x" * 60 + "\n") for i in range(3)]
    shown = scan_mod._bob_scan_files(tmp_path, files)
    assert list(shown) == ["f0.py"]
