@app.get("/api/health", tags=["system"])
async def health() -> dict:
    """System health check — Ollama connectivity, DB, FAISS, model availability."""
    import httpx
    import subprocess

    ollama_ok = False
    tutor_model_present = False
    e4b_available = False
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(f"{settings.OLLAMA_HOST}/api/tags")
            if r.status_code == 200:
                ollama_ok = True
                model_names = [m.get("name", "") for m in r.json().get("models", [])]
                def _model_present(name: str) -> bool:
                    base = name.split(":")[0]
                    return any(n == name or n.split(":")[0] == base for n in model_names)
                tutor_model_present = _model_present(settings.OLLAMA_TUTOR_MODEL)
                e4b_available = _model_present(settings.OLLAMA_MODEL_DEEP)
    except Exception:
        pass
