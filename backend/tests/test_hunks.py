"""
The sandbox harness's forgiving patch applier (sandbox/pyrunner/harness/hunks.py).
It runs inside the container; these tests exercise it on plain temp dirs.
"""

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "hunks",
    Path(__file__).resolve().parents[2]
    / "sandbox"
    / "pyrunner"
    / "harness"
    / "hunks.py",
)
hunks = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(hunks)

_SRC = "def add(a, b):\n    return a - b\n\n\ndef sub(a, b):\n    return a - b\n"


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "calc.py").write_text(_SRC)
    return tmp_path


def test_no_trailing_context_away_from_the_end(repo):
    # git anchors this hunk to the end of the file and refuses it
    hunks.apply(
        repo,
        "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n def add(a, b):\n"
        "-    return a - b\n+    return a + b\n",
    )
    assert (repo / "calc.py").read_text() == _SRC.replace("a - b", "a + b", 1)


def test_wrong_counts_and_line_numbers_use_the_nearest_match(repo):
    hunks.apply(
        repo,
        "--- a/calc.py\n+++ b/calc.py\n@@ -40,9 +40,9 @@\n def sub(a, b):\n"
        "-    return a - b\n+    return a - b  # checked\n",
    )
    assert (repo / "calc.py").read_text().endswith("return a - b  # checked\n")
    assert (
        (repo / "calc.py").read_text().startswith("def add(a, b):\n    return a - b\n")
    )


def test_ambiguous_lines_prefer_the_hinted_location(repo):
    hunks.apply(
        repo,
        "--- a/calc.py\n+++ b/calc.py\n@@ -6 +6 @@\n-    return a - b\n+    return b - a\n",
    )
    text = (repo / "calc.py").read_text()
    assert text.count("return a - b") == 1 and text.endswith("return b - a\n")


def test_new_file(repo):
    hunks.apply(
        repo, "--- /dev/null\n+++ b/pkg/new.py\n@@ -0,0 +1,2 @@\n+x = 1\n+y = 2\n"
    )
    assert (repo / "pkg" / "new.py").read_text() == "x = 1\ny = 2\n"


@pytest.mark.parametrize(
    ("diff", "reason"),
    [
        (
            "--- a/calc.py\n+++ b/calc.py\n@@ -1 +1 @@\n-def mul(a, b):\n+def m():\n",
            "hunk 1 does not match",
        ),
        ("--- a/nope.py\n+++ b/nope.py\n@@ -1 +1 @@\n-x\n+y\n", "no such file"),
        (
            "--- a/../etc/passwd\n+++ b/../etc/passwd\n@@ -1 +1 @@\n-x\n+y\n",
            "outside the repository",
        ),
        ("just prose, no diff", "no file changes"),
    ],
)
def test_refusals_change_nothing(repo, diff, reason):
    with pytest.raises(hunks.PatchError, match=reason):
        hunks.apply(repo, diff)
    assert (repo / "calc.py").read_text() == _SRC
