"""
Behaviour checks for _resolve_hf_asr_model() that must keep working after a fix.

These pass on the original code. A fix that breaks any of them is a regression.
"""

import pytest


@pytest.fixture
def resolve(monkeypatch, tmp_path):
    from app.core.config import settings
    from app.services import whisper_client

    monkeypatch.chdir(tmp_path)

    def _resolve(configured: str) -> str:
        monkeypatch.setattr(settings, "WHISPER_HF_MODEL", configured)
        return whisper_client._resolve_hf_asr_model()

    return _resolve


def test_existing_local_folder_is_used(resolve, tmp_path):
    local = tmp_path / "my-whisper"
    local.mkdir()
    assert resolve(str(local)) == str(local)


def test_hub_id_prefers_downloaded_copy_under_models(resolve, tmp_path):
    cached = tmp_path / "models" / "whisper" / "whisper-tiny"
    cached.mkdir(parents=True)
    assert resolve("openai/whisper-tiny") == "models/whisper/whisper-tiny"


def test_default_hub_id_passes_through(resolve):
    assert resolve("openai/whisper-tiny") == "openai/whisper-tiny"


def test_custom_hub_id_is_not_overridden(resolve):
    assert resolve("distil-whisper/distil-small.en") == "distil-whisper/distil-small.en"
