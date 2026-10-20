"""App settings, read from environment variables / backend/.env."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/.env, regardless of which folder the server is started from
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    # Comma-separated list of origins allowed to call the API (the Next.js app).
    frontend_origin: str = "http://localhost:3000"

    # LLM settings (used from Phase 3 on).
    llm_provider: str = ""
    llm_model: str = ""

    # When true, LLM routes return recorded fixtures instead of calling the provider.
    demo_mode: bool = False

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.frontend_origin.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
