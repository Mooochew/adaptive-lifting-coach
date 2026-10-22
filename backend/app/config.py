"""App settings, read from environment variables / backend/.env."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/.env, regardless of which folder the server is started from
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    # Comma-separated list of origins allowed to call the API (the Next.js app).
    frontend_origin: str = "http://localhost:3000,http://127.0.0.1:3000"

    # AI program review. Provider "" = off (lifters get the engine's baseline); "fake" = scripted
    # replies for tests and e2e. The key is only ever read here, server-side.
    llm_provider: Literal["", "openai", "fake"] = ""
    llm_model: str = "gpt-6.1-sol"
    llm_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    llm_timeout_s: float = 60.0
    llm_max_output_tokens: int = 16000      # includes the model's reasoning tokens
    openai_api_key: SecretStr | None = None

    # When true, LLM routes return recorded fixtures instead of calling the provider.
    demo_mode: bool = False

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.frontend_origin.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
