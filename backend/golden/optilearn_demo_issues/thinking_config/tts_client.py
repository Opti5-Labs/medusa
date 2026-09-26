# Public API
async def speak(text: str, language: str) -> bytes:
    """Single-shot synthesis: returns full WAV bytes."""
    engine, voice_or_model = VOICE_MAP.get(language, ("piper", "en_US-lessac-medium"))
    try:
        if engine == "piper":
            return await _piper_speak(text, voice_or_model)
        return await _mms_speak(text, voice_or_model)
    except Exception as e:
        logger.error("TTS speak() error: {}", e)
        return b""


async def speak_sentences(text: str, language: str) -> AsyncGenerator[bytes, None]:
    """Async generator: yields one WAV per sentence for streaming playback.

    For piper voices, all sentences are submitted to the thread pool upfront so
    subsequent sentences synthesise in parallel while the first is being streamed.
    """
    engine, voice_or_model = VOICE_MAP.get(language, ("piper", "en_US-lessac-medium"))
    sentences = [s for s in split_sentences(text) if s.strip()]
    if not sentences:
        return

    loop = asyncio.get_event_loop()

    if engine == "piper":
        voice_path = Path(settings.VOICES_DIR) / f"{voice_or_model}.onnx"
        if not voice_path.exists():
            voice_path = Path(settings.VOICES_DIR) / "en_US-lessac-medium.onnx"
        if not voice_path.exists():
            logger.error("No voice models found in {}", settings.VOICES_DIR)
            return
        voice_path_str = str(voice_path)
        # Submit all sentences to the executor immediately so they synthesise in parallel.
        futures = [
            loop.run_in_executor(_executor, _piper_speak_sync, voice_path_str, s)
            for s in sentences
        ]
