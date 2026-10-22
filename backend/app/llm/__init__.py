"""Model providers behind one interface (see base.py). `get_llm()` picks one from settings."""

from app.config import Settings
from app.llm.base import InvalidOutput, LLMClient, LLMUnavailable, LLMUsage

__all__ = ["InvalidOutput", "LLMClient", "LLMUnavailable", "LLMUsage", "build_llm"]


def build_llm(settings: Settings) -> LLMClient | None:
    """The configured provider, or None when the AI review is off (lifters get the baseline)."""
    if settings.llm_provider == "fake":
        from app.llm.fake import FakeLLM
        return FakeLLM()
    if settings.llm_provider == "openai":
        if settings.openai_api_key is None or not settings.openai_api_key.get_secret_value().strip():
            return None
        from app.llm.openai_provider import OpenAIProvider
        return OpenAIProvider(api_key=settings.openai_api_key.get_secret_value(), model=settings.llm_model,
                              effort=settings.llm_effort, timeout_s=settings.llm_timeout_s,
                              max_output_tokens=settings.llm_max_output_tokens)
    return None
