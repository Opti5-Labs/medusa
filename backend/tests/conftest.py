"""
pytest configuration — applied to every test in this directory.

Hermetic limits:
    config.py calls load_dotenv() so a developer's backend/.env leaks into
    pytest.  We reset every limit constant (in every module that has already
    imported it) to the public default before each test, then restore it
    afterwards.  This means tests pass identically with and without a local .env.
"""

import pytest

# Public defaults — must match the defaults in app/config.py
_DEFAULTS: dict[str, object] = {
    "MAX_ZIP_SIZE_BYTES": 20 * 1024 * 1024,
    "MAX_UNCOMPRESSED_BYTES": 200 * 1024 * 1024,
    "MAX_ZIP_FILES": 2000,
    "MAX_GITHUB_REPO_SIZE_KB": 50 * 1024,
    "SCAN_MAX_FILES": 40,
    "SCAN_MAX_LINES": 6000,
    "SCAN_TIMEOUT_S": 90,
}

# All modules that import limit constants at module level (add more as needed)
_LIMIT_MODULES = [
    "app.config",
    "app.ingest.safe_extract",
    "app.ingest.zip_upload",
    "app.ingest.github",
    "app.ingest.limits",
    "app.api.scan",
]


@pytest.fixture(autouse=True)
def _hermetic_limits(monkeypatch):
    """Reset all limit constants to public defaults for every test."""
    import importlib

    for mod_name in _LIMIT_MODULES:
        try:
            mod = importlib.import_module(mod_name)
        except ImportError:
            continue
        for name, value in _DEFAULTS.items():
            if hasattr(mod, name):
                monkeypatch.setattr(mod, name, value)
    # Never call the real watsonx.ai from tests, even with a local .env.
    monkeypatch.setattr("app.config.WATSONX_API_KEY", "")
    monkeypatch.setattr("app.config.WATSONX_PROJECT_ID", "")
    # Never run live Bob from tests either (it spends Bobcoins).
    monkeypatch.setattr("app.config.BOB_API_KEY", "")
    monkeypatch.setattr("app.config.BOB_MODE", "live")
    monkeypatch.setattr("app.config.BOB_BINARY", "bob-disabled-in-tests")
    yield
