"""
Tests for ingest/limits.py — scan cap, skipped dirs, binary detection.
All tests are fully offline.
"""

import tempfile
from pathlib import Path

from app.ingest.limits import filter_tree, is_binary

# ── Helpers ───────────────────────────────────────────────────────────────────


def _write(path: Path, content: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        path.write_text(content, encoding="utf-8")
    else:
        path.write_bytes(content)


# ── is_binary ────────────────────────────────────────────────────────────────


def test_is_binary_text_file():
    tmp = Path(tempfile.mkdtemp())
    f = tmp / "hello.py"
    f.write_text("print('hello')", encoding="utf-8")
    assert not is_binary(f)


def test_is_binary_nul_bytes():
    tmp = Path(tempfile.mkdtemp())
    f = tmp / "blob.bin"
    f.write_bytes(b"\x00\x01\x02")
    assert is_binary(f)


# ── filter_tree ──────────────────────────────────────────────────────────────


def test_filter_tree_skips_node_modules():
    with tempfile.TemporaryDirectory() as tmp_s:
        root = Path(tmp_s)
        _write(root / "index.js", "console.log('hi')")
        _write(root / "node_modules" / "lodash" / "index.js", "// lib")
        selected, _warnings = filter_tree(root)
        paths = [str(p.relative_to(root)) for p in selected]
        assert "index.js" in paths
        assert not any("node_modules" in p for p in paths)


def test_filter_tree_skips_binaries():
    with tempfile.TemporaryDirectory() as tmp_s:
        root = Path(tmp_s)
        _write(root / "main.py", "x = 1")
        _write(root / "image.png", b"\x89PNG\r\n\x1a\n")
        selected, _ = filter_tree(root)
        exts = {p.suffix for p in selected}
        assert ".png" not in exts


def test_filter_tree_scan_cap(monkeypatch):
    """When there are more files than SCAN_MAX_FILES, a warning is added."""
    monkeypatch.setattr("app.ingest.limits.SCAN_MAX_FILES", 3)
    with tempfile.TemporaryDirectory() as tmp_s:
        root = Path(tmp_s)
        for i in range(5):
            _write(root / f"file{i}.py", f"x = {i}")
        selected, warnings = filter_tree(root)
        assert len(selected) == 3
        assert any("file cap" in w.lower() for w in warnings)


def test_filter_tree_line_cap(monkeypatch):
    """When the line budget is exhausted, a warning is added and the count is bounded."""
    monkeypatch.setattr("app.ingest.limits.SCAN_MAX_FILES", 100)
    monkeypatch.setattr("app.ingest.limits.SCAN_MAX_LINES", 10)
    with tempfile.TemporaryDirectory() as tmp_s:
        root = Path(tmp_s)
        for i in range(5):
            _write(root / f"file{i}.py", "\n".join([f"x = {j}" for j in range(5)]))
        selected, warnings = filter_tree(root)
        # Each file is 5 lines; at most 2 fit within a 10-line budget
        assert len(selected) <= 2
        assert any("line budget" in w.lower() for w in warnings)


def test_filter_tree_no_source_files():
    """A tree with only binaries/junk still returns empty list without error."""
    with tempfile.TemporaryDirectory() as tmp_s:
        root = Path(tmp_s)
        _write(root / "photo.png", b"\x89PNG\r\n\x1a\n\x00")
        selected, _ = filter_tree(root)
        assert selected == []


def test_filter_tree_lock_files_skipped():
    with tempfile.TemporaryDirectory() as tmp_s:
        root = Path(tmp_s)
        _write(root / "package-lock.json", '{"lockfileVersion":2}')
        _write(root / "Pipfile.lock", "{}")
        _write(root / "main.py", "x = 1")
        selected, _ = filter_tree(root)
        exts = {p.suffix for p in selected}
        assert ".lock" not in exts
        assert ".py" in exts


# ── New tests for fixed filter_tree behaviour ────────────────────────────────


def test_filter_tree_oversized_middle_file_does_not_drop_later_files(monkeypatch):
    """An oversized file in the middle is skipped; smaller files after it are kept."""
    monkeypatch.setattr("app.ingest.limits.SCAN_MAX_FILES", 100)
    monkeypatch.setattr("app.ingest.limits.SCAN_MAX_LINES", 10)
    with tempfile.TemporaryDirectory() as tmp_s:
        root = Path(tmp_s)
        # small.py: 3 lines
        _write(root / "aaa.py", "x = 1\ny = 2\nz = 3")
        # big.py: 20 lines (would push total over 10)
        _write(root / "bbb.py", "\n".join(f"x{i} = {i}" for i in range(20)))
        # small2.py: 3 lines — should still be included after big.py is skipped
        _write(root / "ccc.py", "a = 1\nb = 2\nc = 3")

        selected, warnings = filter_tree(root)
        names = [p.name for p in selected]

        assert "aaa.py" in names, "first small file should be selected"
        assert "bbb.py" not in names, "oversized file should be skipped"
        assert "ccc.py" in names, "small file after oversized one should be selected"
        assert any("line budget" in w.lower() for w in warnings)


def test_filter_tree_code_files_before_docs(monkeypatch):
    """Real code files (.py, .ts) are chosen ahead of .md / .json."""
    monkeypatch.setattr("app.ingest.limits.SCAN_MAX_FILES", 2)
    monkeypatch.setattr("app.ingest.limits.SCAN_MAX_LINES", 10_000)
    with tempfile.TemporaryDirectory() as tmp_s:
        root = Path(tmp_s)
        _write(root / "README.md", "# readme\n" * 5)
        _write(root / "package.json", '{"name":"test"}\n')
        _write(root / "main.py", "x = 1\n")
        _write(root / "index.ts", "const x = 1;\n")

        selected, _ = filter_tree(root)
        assert len(selected) == 2
        exts = {p.suffix for p in selected}
        # The two code files should win over the doc/config files
        assert ".py" in exts
        assert ".ts" in exts


def test_filter_tree_single_cap_message_file_cap(monkeypatch):
    """Exactly one warning message is emitted when the file cap is hit."""
    monkeypatch.setattr("app.ingest.limits.SCAN_MAX_FILES", 2)
    monkeypatch.setattr("app.ingest.limits.SCAN_MAX_LINES", 10_000)
    with tempfile.TemporaryDirectory() as tmp_s:
        root = Path(tmp_s)
        for i in range(5):
            _write(root / f"file{i}.py", f"x = {i}\n")

        _selected, warnings = filter_tree(root)
        assert len(warnings) == 1
        assert "file cap" in warnings[0].lower()


def test_filter_tree_single_cap_message_line_budget(monkeypatch):
    """Exactly one warning message is emitted when the line budget is hit."""
    monkeypatch.setattr("app.ingest.limits.SCAN_MAX_FILES", 100)
    monkeypatch.setattr("app.ingest.limits.SCAN_MAX_LINES", 5)
    with tempfile.TemporaryDirectory() as tmp_s:
        root = Path(tmp_s)
        for i in range(4):
            _write(root / f"file{i}.py", "\n".join(f"x{j} = {j}" for j in range(3)))

        _selected, warnings = filter_tree(root)
        assert len(warnings) == 1
        assert "line budget" in warnings[0].lower()
