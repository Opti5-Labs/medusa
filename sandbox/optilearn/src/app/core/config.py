"""
app/core/config.py — centralised configuration via pydantic-settings BaseSettings.

All other files import `settings` from here. No file may read os.environ directly.
"""
import os
import sys

from pydantic_settings import BaseSettings, SettingsConfigDict

_IS_WINDOWS = sys.platform == "win32"

# Desktop app passes ENV_FILE pointing to the user data dir. Web/dev mode uses
# the default .env next to the project root.
_env_file = os.environ.get("ENV_FILE", ".env")


class Settings(BaseSettings):
    """Application settings loaded from the .env file."""

    model_config = SettingsConfigDict(
        env_file=_env_file,
        env_file_encoding="utf-8",
        case_sensitive=True,
    )

    # Model backend
    USE_LOCAL_OLLAMA: bool = True

    # Gemini API (online fallback when USE_LOCAL_OLLAMA=false and internet available)
    GEMMA_API_KEY: str = ""
    GEMINI_MODEL: str = "gemma-4-31b-it"
    GEMMA_26B_API_KEY: str = ""
    GEMMA_26B_MODEL: str = "gemma-4-31b-it"
    GEMMA_26B_BASE_URL: str = "https://generativelanguage.googleapis.com"

    # Ollama (primary — offline/local)
    OLLAMA_HOST: str = "http://localhost:11434"
    OLLAMA_MODEL_FAST: str = "gemma4:e2b"
    OLLAMA_MODEL_DEEP: str = "gemma4:e4b"
    OLLAMA_TUTOR_MODEL: str = "gemma4:e2b"  # FUTURE: replace with optilearn-gemma4-e2b

    # Language detection
    LANGDETECT_CONFIDENCE_THRESHOLD: float = 0.85

    # Storage
    DB_PATH: str = "./data/optilearn.db"
    FAISS_INDEX_PATH: str = "./data/curriculum.index"
    FAISS_META_PATH: str = "./data/curriculum_meta.json"
    CURRICULUM_DIR: str = "./data/curriculum"

    # Embeddings
    EMBED_MODEL: str = "paraphrase-multilingual-MiniLM-L12-v2"
    HF_LOCAL_FILES_ONLY: bool = True

    # Image processing
    IMAGE_MAX_PX: int = 1024

    # Materials storage
    MATERIALS_DIR: str = "./data/materials"

    # Beyond Presence (AI avatar video — online only)
    BEY_API_KEY: str = ""

    # Whisper / TTS (Phase 2–3, not used yet)
    WHISPER_BINARY: str = "./bin/whisper.exe" if _IS_WINDOWS else "./bin/whisper"
    WHISPER_MODEL: str = "./data/whisper-models/ggml-base.bin"
    WHISPER_HF_MODEL: str = "openai/whisper-tiny"
    PIPER_BINARY: str = "./bin/piper.exe" if _IS_WINDOWS else "./bin/piper"
    VOICES_DIR: str = "./data/voices"

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    FRONTEND_DIST: str = "./frontend/dist"

    # HTTPS (self-signed cert for student microphone access over LAN)
    HTTPS_PORT: int = 8443
    SSL_CERT_PATH: str = "./data/ssl/cert.pem"
    SSL_KEY_PATH: str = "./data/ssl/key.pem"


# Singleton — import this in all other modules
settings = Settings()
