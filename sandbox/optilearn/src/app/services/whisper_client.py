"""
Speech-to-text helpers for Feature 2 live class translation.

Preferred path is whisper.cpp when WHISPER_BINARY and WHISPER_MODEL exist.
Fallback path uses transformers Whisper ASR, which is slower on first load but
keeps local Ollama responsible only for text translation.
"""
from __future__ import annotations

import asyncio
import io
import os
import tempfile
import wave
import uuid
from pathlib import Path

from loguru import logger

from app.core.config import settings

_asr_pipeline = None
_asr_lock = asyncio.Lock()
_asr_loading = False
_asr_error = ""
_asr_backend = ""
_asr_primed = False
_TARGET_SAMPLE_RATE = 16000


def _offline_env() -> None:
    """Block HF network access only in explicit classroom-offline config (HF_LOCAL_FILES_ONLY=true)."""
    if settings.HF_LOCAL_FILES_ONLY:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
    else:
        os.environ.pop("HF_HUB_OFFLINE", None)
        os.environ.pop("TRANSFORMERS_OFFLINE", None)


def _resolve_hf_asr_model() -> str:
    """Prefer local Whisper folders while still allowing a fully cached HF id."""
    configured = Path(settings.WHISPER_HF_MODEL).expanduser()
    if configured.exists():
        return str(configured)

    leaf = settings.WHISPER_HF_MODEL.rsplit("/", 1)[-1]
    slug = settings.WHISPER_HF_MODEL.replace("/", "-")
    for name in (leaf, slug):
        candidate = Path("models") / "whisper" / name
        if candidate.exists():
            return str(candidate)
    return settings.WHISPER_HF_MODEL


def get_transcriber_status() -> dict:
    """Return readiness information for the live translation UI."""
    whisper_binary = Path(settings.WHISPER_BINARY)
    whisper_model = Path(settings.WHISPER_MODEL)
    if whisper_binary.exists() and whisper_model.exists():
        return {
            "ready": True,
            "loading": False,
            "backend": "whisper.cpp",
            "model": str(whisper_model),
            "error": "",
        }
    return {
        "ready": _asr_pipeline is not None and _asr_primed,
        "loading": _asr_loading,
        "backend": _asr_backend or "transformers",
        "model": _resolve_hf_asr_model(),
        "error": _asr_error,
    }


async def warmup_transcriber() -> dict:
    """Load the fallback ASR model ahead of the first class chunk."""
    whisper_binary = Path(settings.WHISPER_BINARY)
    whisper_model = Path(settings.WHISPER_MODEL)
    if whisper_binary.exists() and whisper_model.exists():
        logger.info("Live translation ASR ready via whisper.cpp: {}", whisper_model)
        return get_transcriber_status()
    try:
        await _get_asr_pipeline()
        await _prime_asr_pipeline()
    except Exception:
        return get_transcriber_status()
    return get_transcriber_status()


async def transcribe_chunk(audio_bytes: bytes, hint_language: str | None = None) -> str:
    """Return a transcript for one WAV chunk, or an empty string if unavailable."""
    if not audio_bytes:
        return ""

    whisper_binary = Path(settings.WHISPER_BINARY)
    whisper_model = Path(settings.WHISPER_MODEL)
    if whisper_binary.exists() and whisper_model.exists():
        return await _transcribe_with_whisper_cpp(audio_bytes, hint_language)

    return await _transcribe_with_transformers(audio_bytes, hint_language)


async def _transcribe_with_whisper_cpp(
    audio_bytes: bytes,
    hint_language: str | None,
) -> str:
    tmp_dir = Path(tempfile.gettempdir()) / "optilearn" / "audio"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    wav_path = tmp_dir / f"{uuid.uuid4()}.wav"
    txt_path = wav_path.with_suffix(".txt")
    try:
        wav_path.write_bytes(audio_bytes)
        cmd = [
            settings.WHISPER_BINARY,
            "-m",
            settings.WHISPER_MODEL,
            "-f",
            str(wav_path),
            "--output-txt",
            "--no-timestamps",
        ]
        if hint_language:
            cmd += ["-l", hint_language]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0:
            logger.error("whisper.cpp error: {}", stderr.decode(errors="ignore"))
            return ""
        transcript = txt_path.read_text(encoding="utf-8").strip() if txt_path.exists() else ""
        logger.info("whisper.cpp transcribed {} bytes -> {} chars", len(audio_bytes), len(transcript))
        return transcript
    except asyncio.TimeoutError:
        logger.error("whisper.cpp timed out on chunk")
        return ""
    except Exception as exc:
        logger.error("whisper.cpp transcription error: {!r}", exc)
        return ""
    finally:
        wav_path.unlink(missing_ok=True)
        txt_path.unlink(missing_ok=True)


async def _get_asr_pipeline():
    global _asr_backend, _asr_error, _asr_loading, _asr_pipeline  # noqa: PLW0603
    async with _asr_lock:
        if _asr_pipeline is not None:
            return _asr_pipeline

        def load_pipeline():
            from transformers import pipeline

            _offline_env()
            model_ref = _resolve_hf_asr_model()
            logger.info("Loading Whisper ASR model: {}", model_ref)
            # pipeline() downloads model + processor together, avoiding partial-cache issues
            return pipeline(
                "automatic-speech-recognition",
                model=model_ref,
                device=-1,
            )

        _asr_loading = True
        _asr_error = ""
        _asr_backend = "transformers"
        try:
            _asr_pipeline = await asyncio.to_thread(load_pipeline)
            logger.info("Fallback Whisper ASR model ready")
            return _asr_pipeline
        except Exception as exc:
            _asr_error = (
                f"{exc!r}. Put Whisper in ./models/whisper/ or set WHISPER_HF_MODEL "
                "to a local folder."
            )
            logger.error("Fallback Whisper ASR model load error: {!r}", exc)
            raise
        finally:
            _asr_loading = False


async def _prime_asr_pipeline() -> None:
    global _asr_error, _asr_loading, _asr_primed  # noqa: PLW0603
    if _asr_primed:
        return
    asr = await _get_asr_pipeline()

    def run_prime() -> None:
        import numpy as np

        blank = {
            "array": np.zeros(_TARGET_SAMPLE_RATE, dtype="float32"),
            "sampling_rate": _TARGET_SAMPLE_RATE,
        }
        asr(blank, generate_kwargs={"language": "en", "task": "transcribe"})

    _asr_loading = True
    try:
        await asyncio.to_thread(run_prime)
        _asr_primed = True
        _asr_error = ""
        logger.info("Fallback Whisper ASR smoke test ready")
    except Exception as exc:
        _asr_error = repr(exc)
        logger.error("Fallback Whisper ASR smoke test error: {!r}", exc)
        raise
    finally:
        _asr_loading = False


def _resample_audio(audio, source_rate: int):
    """Resample mono float32 audio to Whisper's 16 kHz input without torchaudio."""
    import numpy as np

    if source_rate == _TARGET_SAMPLE_RATE or audio.size == 0:
        return np.ascontiguousarray(audio, dtype="float32")
    if source_rate <= 0:
        raise ValueError(f"Invalid WAV sample rate: {source_rate}")
    if audio.size == 1:
        return np.ascontiguousarray(audio, dtype="float32")

    target_length = max(1, int(round(audio.size * _TARGET_SAMPLE_RATE / source_rate)))
    source_positions = np.arange(audio.size, dtype="float32")
    target_positions = np.linspace(0, audio.size - 1, target_length, dtype="float32")
    resampled = np.interp(target_positions, source_positions, audio).astype("float32")
    return np.ascontiguousarray(resampled)


def _wav_bytes_to_array(audio_bytes: bytes) -> dict:
    """Decode browser-generated PCM WAV bytes without ffmpeg or torchaudio."""
    import numpy as np

    with wave.open(io.BytesIO(audio_bytes), "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        sample_rate = wav.getframerate()
        frames = wav.readframes(wav.getnframes())

    if sample_width == 2:
        audio = np.frombuffer(frames, dtype="<i2").astype("float32") / 32768.0
    elif sample_width == 4:
        audio = np.frombuffer(frames, dtype="<i4").astype("float32") / 2147483648.0
    elif sample_width == 1:
        audio = (np.frombuffer(frames, dtype="uint8").astype("float32") - 128.0) / 128.0
    else:
        raise ValueError(f"Unsupported WAV sample width: {sample_width}")

    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)

    audio = _resample_audio(audio, sample_rate)
    return {"array": audio, "sampling_rate": _TARGET_SAMPLE_RATE}


def _has_enough_audio_energy(audio) -> bool:
    """Avoid running ASR on near-silent chunks that Whisper may hallucinate."""
    import numpy as np

    if audio.size < _TARGET_SAMPLE_RATE // 2:
        return False
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    rms = float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0
    return peak >= 0.01 and rms >= 0.0025


async def _transcribe_with_transformers(
    audio_bytes: bytes,
    hint_language: str | None,
) -> str:
    try:
        audio_input = _wav_bytes_to_array(audio_bytes)
        if not _has_enough_audio_energy(audio_input["array"]):
            logger.info("Whisper ASR skipped quiet chunk ({} bytes)", len(audio_bytes))
            return ""
        asr = await _get_asr_pipeline()

        def run_asr() -> str:
            kwargs = {}
            if hint_language:
                kwargs["generate_kwargs"] = {"language": hint_language, "task": "transcribe"}
            result = asr(audio_input, **kwargs)
            return str(result.get("text", "")).strip()

        transcript = await asyncio.to_thread(run_asr)
        logger.info("Whisper ASR transcribed {} bytes -> {} chars", len(audio_bytes), len(transcript))
        return transcript
    except Exception as exc:
        logger.error("Whisper ASR transcription error: {!r}", exc)
        return ""
