def load_user_network_settings() -> None:
    """Load persisted network-mode preference at server startup.

    Dashboard toggle always wins: a teacher-set offline/auto preference
    persists across restarts. .env sets the default only when no saved
    preference exists.
    """
    global _runtime_network_mode  # noqa: PLW0603
    try:
        if not _USER_SETTINGS_PATH.exists():
            # No saved preference — fall back to .env default
            _runtime_network_mode = "offline" if settings.USE_LOCAL_OLLAMA else "auto"
            return
        data = json.loads(_USER_SETTINGS_PATH.read_text(encoding="utf-8"))
        mode = data.get("network_mode")
        if mode in {"auto", "offline"}:
            _runtime_network_mode = mode
        else:
            _runtime_network_mode = "offline" if settings.USE_LOCAL_OLLAMA else "auto"
    except Exception as exc:
        logger.warning("Could not load user network settings: {}", exc)
        _runtime_network_mode = "offline" if settings.USE_LOCAL_OLLAMA else "auto"


def get_network_mode() -> str:
    if _runtime_network_mode in {"auto", "offline"}:
        return _runtime_network_mode
    return "offline" if settings.USE_LOCAL_OLLAMA else "auto"


def _is_force_offline() -> bool:
    return get_network_mode() == "offline"


def is_force_offline() -> bool:
    """Public alias for _is_force_offline — use this for cross-module imports."""
    return _is_force_offline()


def has_26b_api_key() -> bool:
    """Public alias for _has_26b_api_key — use this for cross-module imports."""
    return _has_26b_api_key()


def set_network_mode(mode: str) -> dict:
    """Persist runtime network mode and invalidate the routing cache."""
    global _runtime_network_mode  # noqa: PLW0603
    if mode not in {"auto", "offline"}:
        raise ValueError("mode must be 'auto' or 'offline'")
    _runtime_network_mode = mode
    _USER_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _USER_SETTINGS_PATH.write_text(
        json.dumps({"network_mode": mode}, indent=2),
        encoding="utf-8",
    )
    invalidate_network_cache()
    return {"mode": get_network_mode(), "use_local_ollama": _is_force_offline()}


async def check_network_status() -> dict:
    """
    Returns network status based on USE_LOCAL_OLLAMA setting.
    Returns: {
        "connected": bool,
        "latency_ms": int | None,
        "use_26b": bool
    }
    """
    # Determine status purely based on USE_LOCAL_OLLAMA setting
    if settings.USE_LOCAL_OLLAMA:
        # Offline mode: using local ollama
        return {"connected": True, "latency_ms": None, "use_26b": False}
    else:
        # Online mode: using API/cloud
        return {"connected": True, "latency_ms": None, "use_26b": True}


async def _measure_network_latency() -> int | None:
    """Measure internet latency with HTTP probes, falling back to OS ping."""
