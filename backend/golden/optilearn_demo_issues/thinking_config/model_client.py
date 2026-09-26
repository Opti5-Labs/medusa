def _get_26b_client() -> Any:
    from google import genai

    return genai.Client(api_key=settings.GEMMA_26B_API_KEY)


async def stream_26b(
    prompt: str,
    system_prompt: str = "",
    enable_thinking: bool = False,
    image_b64: str | None = None,
) -> AsyncGenerator[str, None]:
    """Stream tokens from Gemma 4 26B via Google Generative AI API."""
    import base64
    from google.genai import types as genai_types

    client = _get_26b_client()
    # Image-first ordering improves extraction quality for vision models
    if image_b64:
        parts: list[Any] = [
            genai_types.Part(
                inline_data=genai_types.Blob(
                    mime_type="image/jpeg",
                    data=base64.b64decode(image_b64),
                )
            ),
            genai_types.Part(text=prompt),
        ]
    else:
        parts = [genai_types.Part(text=prompt)]
    contents = [genai_types.Content(role="user", parts=parts)]
    thinking_cfg_kwargs: dict[str, Any] = {"include_thoughts": enable_thinking}
    if not enable_thinking:
        thinking_cfg_kwargs["thinking_budget"] = 0
    try:
        thinking_cfg = genai_types.ThinkingConfig(**thinking_cfg_kwargs)
    except TypeError:
        thinking_cfg = genai_types.ThinkingConfig(include_thoughts=enable_thinking)

    config_kwargs: dict[str, Any] = {
        "temperature": 1.0 if enable_thinking else 0.2,
        "top_p": 0.95,
        "top_k": 64,
        "thinking_config": thinking_cfg,
    }
    if not enable_thinking:
        config_kwargs["max_output_tokens"] = 2048 if image_b64 else 1024
    if system_prompt:
        config_kwargs["system_instruction"] = system_prompt
    config = genai_types.GenerateContentConfig(**config_kwargs)
    logger.info(
        "26B API stream start -> model={} prompt_chars={} image={}",
        settings.GEMMA_26B_MODEL,
        len(prompt),
        bool(image_b64),
    )
    for attempt in range(_API_MAX_RETRIES):
        try:
            stream = await asyncio.to_thread(
                client.models.generate_content_stream,
                model=settings.GEMMA_26B_MODEL,
                contents=contents,
                config=config,
            )
            chunk_count = 0
            char_count = 0
            for chunk in stream:
                if getattr(chunk, "text", None):
