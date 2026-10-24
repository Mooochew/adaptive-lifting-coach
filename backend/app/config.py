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
    llm_timeout_s: float = 60.0             # one model call at most
    llm_review_deadline_s: float = 90.0     # a whole review (all attempts); then the standard plan
    llm_max_output_tokens: int = 8000       # per call, includes the model's reasoning tokens
    openai_api_key: SecretStr | None = None

    # AI review limits (each review may make up to 3 paid model calls). Over a limit, lifters
    # get the standard plan instead.
    ai_reviews_per_window: int = 5
    ai_review_window_s: float = 600
    ai_reviews_per_day: int = 200
    ai_max_concurrent_reviews: int = 4      # more at once get the standard plan (keeps the API responsive)

    # Demo mode: replay AI replies recorded in demo_dir; a request never seen before goes to the
    # live model above (if any) and is recorded. Rehearse once, then the demo runs offline. See
    # app/llm/replay.py.
    demo_mode: bool = False
    demo_dir: str = str(Path(__file__).resolve().parent.parent / "demo" / "recordings")

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.frontend_origin.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
