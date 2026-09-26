"""The architecture pipeline's parse step: hostile files and the time budget."""

import time
from pathlib import Path

from app.architecture.inventory import FileRecord
from app.pipelines import architecture as pipeline


def _record(root: Path, rel: str, text: str) -> FileRecord:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return FileRecord(
        rel_path=rel,
        abs_path=path,
        ext=".py",
        size=len(text),
        lines=text.count("\n"),
        language="python",
        is_config_doc=False,
        is_test=False,
        is_generated=False,
    )


def test_one_hostile_file_is_skipped_and_the_rest_are_parsed(tmp_path):
    files = [
        _record(tmp_path, "app/a.py", "import os\n"),
        _record(tmp_path, "app/evil.py", "x = a" + ".b" * 190_000 + "\n"),
        _record(tmp_path, "app/b.py", "from app import a\n"),
    ]
    facts, failures, stopped = pipeline._parse_files(files, time.monotonic() + 60)
    assert sorted(facts) == ["app/a.py", "app/b.py"]
    assert failures == 1
    assert stopped is False


def test_parsing_stops_at_the_deadline(tmp_path):
    files = [_record(tmp_path, f"m{i}.py", "x = 1\n") for i in range(3)]
    facts, failures, stopped = pipeline._parse_files(files, time.monotonic() - 1)
    assert facts == {}
    assert failures == 0
    assert stopped is True
