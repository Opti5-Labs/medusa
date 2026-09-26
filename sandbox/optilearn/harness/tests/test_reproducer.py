"""
Medusa reproducer for the OptiLearn Whisper fallback bug.

When WHISPER_HF_MODEL points at a local folder that does not exist,
_resolve_hf_asr_model() must not hand the raw path to Hugging Face, which
rejects it as an invalid repo id and crashes the ASR fallback on startup.

Fails on the buggy code, passes once the fallback resolves to a valid Hub id.
"""

from pathlib import Path

from huggingface_hub.errors import HFValidationError
from huggingface_hub.utils import validate_repo_id


def _is_loadable(model_ref: str) -> bool:
    if Path(model_ref).exists():
        return True
    try:
        validate_repo_id(model_ref)
    except HFValidationError:
        return False
    return True


def test_missing_local_whisper_path_resolves_to_valid_model(monkeypatch, tmp_path):
    from app.core.config import settings
    from app.services import whisper_client

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings, "WHISPER_HF_MODEL", "./models/whisper/openai-whisper-tiny")

    resolved = whisper_client._resolve_hf_asr_model()

    assert _is_loadable(resolved), (
        f"_resolve_hf_asr_model() returned {resolved!r}, which is neither an existing "
        "folder nor a valid Hugging Face repo id"
    )
