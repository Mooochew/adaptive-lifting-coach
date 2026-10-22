"""What every model provider looks like to the rest of the app.

A provider turns (system prompt, user message, response schema) into a parsed, schema-valid
object. Everything provider-specific (SDK calls, structured-output mode, caching, error types)
stays inside the adapter, so switching models is one new adapter plus config.

Errors, as refine.py expects them:
  InvalidOutput    the reply can't be used (cut off, no JSON): the AI gets another try with feedback
  LLMUnavailable   outage, timeout, refusal, bad credentials: don't retry, use the baseline
"""

from dataclasses import dataclass, field
from typing import Protocol, TypeVar

from pydantic import BaseModel

from app.refine import InvalidOutput

T = TypeVar("T", bound=BaseModel)

__all__ = ["InvalidOutput", "LLMClient", "LLMUnavailable", "LLMUsage"]


class LLMUnavailable(RuntimeError):
    """The model can't be used right now; the message is for server logs, not the lifter."""


@dataclass
class LLMUsage:
    """Running totals across calls, for logs and the eval script."""
    calls: int = 0
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    seconds: float = 0.0
    per_call_seconds: list[float] = field(default_factory=list)


class LLMClient(Protocol):
    name: str                 # e.g. "openai:gpt-6.1-sol", for logs and eval reports
    usage: LLMUsage

    def generate(self, *, system: str, user: str, schema: type[T]) -> T:
        """Return the reply parsed into `schema`, or raise InvalidOutput / LLMUnavailable."""
        ...
