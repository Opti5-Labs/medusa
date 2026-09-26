"""Tests for the OptiLearn demo scenario: splicing, diffs, prepared candidates."""

import pytest

from app.demo import optilearn


def test_target_function_is_the_buggy_version():
    source = optilearn.target_function_source()
    assert source.startswith("def _resolve_hf_asr_model")
    assert "return settings.WHISPER_HF_MODEL" in source
    assert "openai/whisper-tiny" not in source  # the fix is not present


@pytest.mark.parametrize(
    "candidate", optilearn.load_prepared(), ids=lambda c: c.approach[:20]
)
def test_prepared_candidates_splice_cleanly(candidate):
    patched = optilearn.splice(candidate.source)
    diff = optilearn.unified_diff(patched)
    stats = optilearn.patch_stats(diff)
    assert stats.files_changed == 1
    assert stats.lines_added >= 1
    # Only the target function changed
    assert "def warmup_transcriber" in patched


def test_splice_rejects_invalid_python():
    with pytest.raises(optilearn.CandidateRejected, match="not valid Python"):
        optilearn.splice("def _resolve_hf_asr_model(:\n    pass\n")


def test_splice_rejects_other_code():
    with pytest.raises(optilearn.CandidateRejected, match="exactly one definition"):
        optilearn.splice("import os\ndef _resolve_hf_asr_model():\n    return 'x'\n")
    with pytest.raises(optilearn.CandidateRejected):
        optilearn.splice("def something_else():\n    return 1\n")


def test_make_workdir_writes_patch_without_touching_bundle(tmp_path):
    patched = optilearn.splice(optilearn.load_prepared()[0].source)
    workdir = optilearn.make_workdir(tmp_path, "c1", patched)
    assert (workdir / optilearn.TARGET_FILE).read_text() == patched
    assert optilearn.read_target() != patched  # bundled source unchanged
    assert (workdir / "tests" / "test_system_optimization.py").exists()


def test_zip_tree_contains_patched_file(tmp_path):
    import io
    import zipfile

    patched = optilearn.splice(optilearn.load_prepared()[0].source)
    workdir = optilearn.make_workdir(tmp_path, "c1", patched)
    with zipfile.ZipFile(io.BytesIO(optilearn.zip_tree(workdir))) as zf:
        name = f"optilearn/{optilearn.TARGET_FILE}"
        assert zf.read(name).decode() == patched
        assert not any(n.endswith(".env") for n in zf.namelist())


def test_splice_rejects_unchanged_function():
    original = optilearn.target_function_source()
    with pytest.raises(optilearn.CandidateRejected, match="unchanged"):
        optilearn.splice(original)
    # Comment-only edits are still no change.
    commented = original.replace(
        "    return settings.WHISPER_HF_MODEL",
        "    return settings.WHISPER_HF_MODEL  # same",
    )
    with pytest.raises(optilearn.CandidateRejected, match="unchanged"):
        optilearn.splice(commented)
