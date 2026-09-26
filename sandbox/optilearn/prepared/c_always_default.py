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
    return "openai/whisper-tiny"
