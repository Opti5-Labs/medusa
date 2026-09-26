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

    from huggingface_hub.errors import HFValidationError
    from huggingface_hub.utils import validate_repo_id

    try:
        validate_repo_id(settings.WHISPER_HF_MODEL)
    except HFValidationError:
        logger.warning(
            "WHISPER_HF_MODEL {!r} is not a local folder or a valid Hub id; "
            "using openai/whisper-tiny",
            settings.WHISPER_HF_MODEL,
        )
        return "openai/whisper-tiny"
    return settings.WHISPER_HF_MODEL
