from pathlib import Path

import pytest

from app import config
from app.models.contracts import Issue, ScanResult
from app.pipelines import ask
from app.store import ScanRecord


def _issue(
    n: int,
    priority: str = "Medium",
    file: str | None = None,
    line: int | None = None,
    **kw,
) -> Issue:
    return Issue(
        id=f"i{n}",
        title=f"Issue {n}",
        description="d",
        priority=priority,  # type: ignore[arg-type]
        source="scan",
        file=file,
        line=line,
        **kw,
    )


def _record(
    issues: list[Issue] | None = None,
    root: Path | None = None,
    source: str = "zip",
    warnings: list[str] | None = None,
    scanned: int = 3,
    total: int = 9,
) -> ScanRecord:
    result = ScanResult(
        scan_id="s1",
        repo_source=source,  # type: ignore[arg-type]
        language="Python",
        files_scanned=[f"f{i}.py" for i in range(scanned)],
        files_total=total,
        issues=issues or [],
        warnings=warnings or [],
    )
    return ScanRecord(scan_id="s1", tmp_dir=None, result=result, root=root)


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ── classify ──────────────────────────────────────────────────────────────────

INSTANT = [
    "What are the issues?",
    "what are the issues in this repo",
    "Which bugs are there?",
    "what issues are found",
    "List all the issues",
    "show me the bugs.",
    "Give me all findings",
    "Any issues?",
    "how many issues are there",
    "Bugs",
]

MODEL = [
    "what are the issues and how do I fix them",
    "why does the login fail",
    "how many issues are caused by the parser",
    "explain the issues",
    "what are the bugs because of the cache",
    "show me the bugs and patch them",
    "what does whisper_client do",
    "which file handles uploads",
    "list the files",
    "what are the issues in utils.py",
    "hello",
    "",
]


@pytest.mark.parametrize("question", INSTANT)
def test_classify_instant(question: str) -> None:
    assert ask.classify(question, None) == "issue_list"


@pytest.mark.parametrize("question", MODEL)
def test_classify_model(question: str) -> None:
    assert ask.classify(question, None) == "model"


def test_classify_scoped_question_always_goes_to_model() -> None:
    assert ask.classify("What are the issues?", "i1") == "model"


# ── issue_list_answer ─────────────────────────────────────────────────────────


def test_issue_list_orders_by_priority_and_formats_lines() -> None:
    record = _record(
        [
            _issue(1, "Low", "a.py", 3, category="performance", found_by="granite"),
            _issue(2, "High", "b/c.py", 10, category="security", found_by="bob"),
            _issue(3, "Medium", "d.py"),
        ]
    )
    out = ask.issue_list_answer(record, "a1", "what are the issues")
    lines = out.answer.splitlines()
    assert lines[0] == (
        "Found **3 issues** in this scan (Python; 3 of 9 eligible files analysed)."
    )
    assert lines[2] == "1. **High**: Issue 2, `b/c.py:10`"
    assert lines[3] == "   security | found by Bob"
    assert lines[4] == "2. **Medium**: Issue 3, `d.py`"
    assert lines[5] == "3. **Low**: Issue 1, `a.py:3`"
    assert lines[6] == "   performance | found by Granite"
    assert out.answer.endswith("open an issue to reproduce and debug it.")
    assert (out.grounding, out.answered_by, out.files_read) == ("scan_data", "scan", [])
    assert [(c.file, c.line) for c in out.citations] == [
        ("b/c.py", 10),
        ("d.py", None),
        ("a.py", 3),
    ]


def test_issue_list_zero_issues_is_honest_and_lists_two_warnings() -> None:
    record = _record([], warnings=["w1", "w2", "w3"], scanned=4, total=50)
    out = ask.issue_list_answer(record, "a1", "any issues")
    assert out.answer.startswith(
        "No issues were found in the 4 files analysed. That does not prove the "
        "rest of the code is bug-free: the scan analysed 4 of 50 eligible files."
    )
    assert "w1" in out.answer and "w2" in out.answer and "w3" not in out.answer
    assert out.citations == []


# ── retrieval ─────────────────────────────────────────────────────────────────


def _big_repo(root: Path) -> None:
    for i in range(59):
        _write(root, f"pkg/mod{i:02d}.py", f"def helper_{i}():\n    return {i}\n")
    _write(
        root,
        "zz_last.py",
        "def compute_whisper_offset(x):\n    return x - 1\n",
    )


def test_named_function_is_found_beyond_the_scan_cap(tmp_path: Path) -> None:
    _big_repo(tmp_path)
    paths = ask.list_repo_files(tmp_path, "")
    assert len(paths) == 60 and paths[-1] == "zz_last.py"
    picked = ask.pick_files(
        tmp_path, "", paths, "why does compute_whisper_offset go wrong?", None
    )
    assert picked[0].path == "zz_last.py"
    assert len(picked) <= config.ASK_MAX_FILES


def test_issue_scope_and_terms_influence_the_pick(tmp_path: Path) -> None:
    _big_repo(tmp_path)
    paths = ask.list_repo_files(tmp_path, "")
    issue = _issue(1, file="pkg/mod07.py")
    picked = ask.pick_files(tmp_path, "", paths, "what is wrong here", issue)
    assert picked[0].path == "pkg/mod07.py"


def test_traversal_question_never_reads_outside_the_root(tmp_path: Path) -> None:
    root = tmp_path / "work" / "repo"
    _write(root, "main.py", "print('hi')\n")
    (tmp_path / "secret.txt").write_text("SENTINEL_OUTSIDE", encoding="utf-8")
    paths = ask.list_repo_files(root, "")
    picked = ask.pick_files(root, "", paths, "show ../../secret.txt please", None)
    assert all("SENTINEL_OUTSIDE" not in p.text for p in picked)
    assert ask.read_file(root, "", "../../secret.txt") is None
    assert ask.read_file(root, "", "../secret.txt") is None


def test_symlink_pointing_outside_is_not_readable(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write(root, "main.py", "print('hi')\n")
    outside = tmp_path / "outside.txt"
    outside.write_text("SENTINEL_OUTSIDE", encoding="utf-8")
    try:
        (root / "link.txt").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks cannot be created on this system")
    assert ask.read_file(root, "", "link.txt") is None
    picked = ask.pick_files(
        root, "", ask.list_repo_files(root, ""), "SENTINEL_OUTSIDE link", None
    )
    assert all("SENTINEL_OUTSIDE" not in p.text for p in picked)


def test_junk_binary_and_minified_files_are_never_picked(tmp_path: Path) -> None:
    term = "compute_whisper_offset"
    _write(tmp_path, "src/real.py", f"def {term}(): pass\n")
    _write(tmp_path, f"node_modules/dep/{term}.py", f"def {term}(): pass\n")
    _write(tmp_path, f".hidden/{term}.py", f"def {term}(): pass\n")
    _write(tmp_path, f"build/{term}.py", f"def {term}(): pass\n")
    _write(tmp_path, "static/app.min.js", f"var {term}=1;\n")
    _write(tmp_path, "static/wide.js", f"var {term}=1; " + "x" * 600 + "\n")
    (tmp_path / "blob.py").write_bytes(f"{term}".encode() + b"\x00\x01")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG")
    paths = ask.list_repo_files(tmp_path, "")
    assert paths == ["src/real.py"]
    picked = ask.pick_files(tmp_path, "", paths, f"where is {term}", None)
    assert [p.path for p in picked] == ["src/real.py"]


def test_file_listing_puts_code_before_docs_before_others(tmp_path: Path) -> None:
    for name in ("z.py", "a.md", "b.txt", "c.weird", "a.py"):
        _write(tmp_path, name, "x\n")
    assert ask.list_repo_files(tmp_path, "pre/") == [
        "pre/a.py",
        "pre/z.py",
        "pre/a.md",
        "pre/b.txt",
        "pre/c.weird",
    ]


def test_index_is_capped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "ASK_MAX_INDEX_FILES", 3)
    for i in range(6):
        _write(tmp_path, f"f{i}.py", "x\n")
    assert len(ask.list_repo_files(tmp_path, "")) == 3


def test_search_byte_budget_stops_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path, "a.py", "filler\n" * 100)
    _write(tmp_path, "b.py", "compute_whisper_offset\n")
    monkeypatch.setattr(config, "ASK_MAX_SEARCH_BYTES", 10)
    paths = ask.list_repo_files(tmp_path, "")
    picked = ask.pick_files(tmp_path, "", paths, "compute_whisper_offset", None)
    assert picked == [] or all(p.path != "b.py" for p in picked)


def test_ties_prefer_shorter_text_then_path(tmp_path: Path) -> None:
    _write(tmp_path, "b.py", "needle_value = 1\n")
    _write(tmp_path, "a.py", "needle_value = 1\n")
    _write(tmp_path, "c.py", "needle_value = 1\n# padding\n")
    paths = ask.list_repo_files(tmp_path, "")
    picked = ask.pick_files(tmp_path, "", paths, "needle_value", None)
    assert [p.path for p in picked] == ["a.py", "b.py", "c.py"]


def test_fallback_picks_readme_first_then_entry_points(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "# Project\n")
    _write(tmp_path, "main.py", "print(1)\n")
    _write(tmp_path, "server.py", "print(2)\n")
    _write(tmp_path, "cli.py", "print(3)\n")
    _write(tmp_path, "other.py", "print(4)\n")
    paths = ask.list_repo_files(tmp_path, "")
    picked = ask.pick_files(tmp_path, "", paths, "qqq zzz", None)
    assert [p.path for p in picked] == ["README.md", "cli.py", "main.py"]
    assert all(p.score == 0 for p in picked)


def test_question_terms() -> None:
    plain, symbols = ask.question_terms(
        "What does parseConfig do in the settings module, with max_retries?"
    )
    assert plain == {"parseconfig", "settings", "module", "max_retries"}
    assert symbols == {"parseconfig", "settings", "max_retries"}
    assert symbols <= plain
    assert not (plain & ask.STOPWORDS)


def test_question_terms_long_words_are_symbols() -> None:
    plain, symbols = ask.question_terms("uploading zip")
    assert "uploading" in symbols and "zip" not in symbols and "zip" in plain


def test_score_file_rules() -> None:
    def score(path="pkg/a.py", text="", q="", plain=(), sym=(), scoped=None) -> int:
        return ask.score_file(path, text, q, set(plain), set(sym), scoped)

    assert score() == 0
    assert score(q="look at pkg/a.py") == 100
    assert score(q="look at A.PY".lower()) == 100
    assert score(scoped="pkg/a.py") == 60
    assert (
        score(
            path="alpha/beta/gamma/delta.py", plain=["alpha", "beta", "gamma", "delta"]
        )
        == 15
    )
    assert (
        score(
            text="aa bb cc dd ee ff gg", sym=["aa", "bb", "cc", "dd", "ee", "ff", "gg"]
        )
        == 40
    )
    assert score(text="foo", plain=["foo"]) == 1


def test_score_file_plain_content_is_capped_at_ten() -> None:
    words = [f"word{i:02d}" for i in range(15)]
    got = ask.score_file("x.py", " ".join(words), "", set(words), set(), None)
    assert got == 10


# ── excerpts ──────────────────────────────────────────────────────────────────


def _numbered(n: int, hits: dict[int, str] | None = None) -> str:
    hits = hits or {}
    return "\n".join(hits.get(i, f"line {i}") for i in range(1, n + 1))


def test_small_file_is_whole() -> None:
    ex = ask.make_excerpts(ask.Picked("a.py", _numbered(300), 1), {"needle"})
    assert len(ex) == 1 and ex[0].start == 1 and len(ex[0].lines) == 300


def test_big_file_is_windowed_with_real_line_numbers() -> None:
    text = _numbered(1000, {500: "the needle is here"})
    (ex,) = ask.make_excerpts(ask.Picked("a.py", text, 1), {"needle"})
    assert ex.start == 470 and len(ex.lines) == 61
    assert ex.lines[500 - ex.start] == "the needle is here"


def test_overlapping_windows_merge_and_distant_ones_stay_apart() -> None:
    text = _numbered(1000, {100: "needle a", 120: "needle b", 700: "needle c"})
    ex = ask.make_excerpts(ask.Picked("a.py", text, 1), {"needle"})
    assert [(e.start, e.start + len(e.lines) - 1) for e in ex] == [
        (70, 150),
        (670, 730),
    ]


def test_only_first_four_hits_get_windows() -> None:
    hits = {i * 100: "needle" for i in range(1, 9)}
    ex = ask.make_excerpts(ask.Picked("a.py", _numbered(1000, hits), 1), {"needle"})
    assert len(ex) == 4


def test_window_is_clamped_at_file_start() -> None:
    (ex,) = ask.make_excerpts(
        ask.Picked("a.py", _numbered(1000, {5: "needle"}), 1), {"needle"}
    )
    assert ex.start == 1 and len(ex.lines) == 35


def test_no_term_in_big_file_falls_back_to_head() -> None:
    (ex,) = ask.make_excerpts(ask.Picked("a.py", _numbered(1000), 1), {"needle"})
    assert ex.start == 1 and len(ex.lines) == 120


def test_pack_reports_truncation() -> None:
    small = ask.Picked("a.py", "x" * 9, 5)
    other = ask.Picked("b.py", "y" * 9, 4)
    packed, truncated = ask.pack_excerpts([small, other], set(), budget=100)
    assert [e.path for e in packed] == ["a.py", "b.py"] and not truncated
    packed, truncated = ask.pack_excerpts([small, other], set(), budget=15)
    assert [e.path for e in packed] == ["a.py"] and truncated


def test_pack_cuts_a_lone_oversized_excerpt_instead_of_sending_nothing() -> None:
    big = ask.Picked("a.py", "\n".join(["x" * 9] * 10), 5)
    packed, truncated = ask.pack_excerpts([big], set(), budget=35)
    assert truncated and len(packed) == 1 and len(packed[0].lines) == 3


def test_repo_map_caps_and_states_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "ASK_MAX_REPO_MAP_FILES", 2)
    assert ask.repo_map(["a", "b", "c"]) == (
        "Repository map (3 files, showing first 2):\na\nb"
    )


# ── demo ──────────────────────────────────────────────────────────────────────


def test_demo_record_maps_to_the_bundled_source() -> None:
    record = _record(source="demo", root=None)
    root, prefix = ask.repo_root(record)
    assert (root, prefix) == (config.OPTILEARN_SRC, "optilearn/")
    paths = ask.list_repo_files(root, prefix)
    assert "optilearn/app/services/whisper_client.py" in paths
    picked = ask.pick_files(
        root, prefix, paths, "How does the Whisper model get loaded?", None
    )
    assert picked[0].path == "optilearn/app/services/whisper_client.py"


# ── redaction ─────────────────────────────────────────────────────────────────


def test_each_secret_pattern_is_masked() -> None:
    aws = "AKIA" + "0123456789ABCDEF"
    pem = (
        "-----BEGIN RSA PRIVATE " + "KEY-----\nabc\ndef\n-----END RSA PRIVATE KEY-----"
    )
    ghp = "ghp_" + "a1" * 18
    gho = "gho_" + "B" * 30
    pat = "github_pat_" + "c" * 40
    sk = "sk-" + "d" * 24
    text = f"a {aws} b\n{pem}\nc {ghp} {gho} {pat} {sk}"
    out = ask.redact(text)
    for secret in (aws, "abc\ndef", ghp, gho, pat, sk):
        assert secret not in out
    assert out.count("[REDACTED]") == 6


def test_assignment_values_are_masked_but_names_kept() -> None:
    text = (
        'API_KEY = "hunter2hunter2"\n'
        "client_secret: 'topsecretvalue'\n"
        '{"password": "p@ss", "db_passwd": "x1"}\n'
        'auth_token="abc"\n'
        'apikey = "zzz"'
    )
    out = ask.redact(text)
    assert 'API_KEY = "[REDACTED]"' in out
    assert "client_secret: '[REDACTED]'" in out
    assert '"password": "[REDACTED]"' in out
    assert '"db_passwd": "[REDACTED]"' in out
    assert 'auth_token="[REDACTED]"' in out
    assert 'apikey = "[REDACTED]"' in out
    for leaked in ("hunter2", "topsecret", "p@ss", "abc", "zzz"):
        assert leaked not in out


def test_ordinary_code_is_unchanged() -> None:
    code = (
        "def total(items):\n"
        "    sku = 'ASK-1'\n"
        "    if mode == 'password':\n"
        "        token = tokenize(items)\n"
        "    return sum(i.price for i in items)  # AKIA is a prefix, sk-short\n"
    )
    assert ask.redact(code) == code


# ── repo_root ─────────────────────────────────────────────────────────────────


def test_repo_root_returns_the_extracted_tree(tmp_path: Path) -> None:
    assert ask.repo_root(_record(root=tmp_path)) == (tmp_path, "")


def test_repo_root_raises_when_the_directory_is_gone(tmp_path: Path) -> None:
    with pytest.raises(ask.AskUnavailable, match="no longer available"):
        ask.repo_root(_record(root=tmp_path / "gone"))


def test_repo_root_raises_when_there_is_no_root() -> None:
    with pytest.raises(ask.AskUnavailable, match="run the scan again"):
        ask.repo_root(_record(root=None, source="github"))


def test_read_file_truncates_and_tolerates_bad_bytes(tmp_path: Path) -> None:
    (tmp_path / "big.txt").write_bytes(b"a" * 70_000)
    (tmp_path / "bad.txt").write_bytes(b"ok \xff\xfe end")
    assert len(ask.read_file(tmp_path, "", "big.txt") or "") == 60_000
    assert (ask.read_file(tmp_path, "", "bad.txt") or "").startswith("ok ")
    assert ask.read_file(tmp_path, "pre/", "other/bad.txt") is None
    assert ask.read_file(tmp_path, "pre/", "pre/bad.txt") is not None
