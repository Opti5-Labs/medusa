def _has_26b_api_key() -> bool:
    key = (settings.GEMMA_26B_API_KEY or "").strip()
    return bool(key and not key.startswith("<"))


async def is_model_present(model_name: str) -> bool:
    """Check if a model is already pulled in Ollama (no download triggered)."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{settings.OLLAMA_HOST}/api/tags")
        if r.status_code != 200:
            return False
        names = [m.get("name", "") for m in r.json().get("models", [])]
        # Match exact name or name without tag (e.g. "gemma4:e2b" or "gemma4")
        base = model_name.split(":")[0]
        return any(n == model_name or n.split(":")[0] == base for n in names)
    except Exception:
        return False
