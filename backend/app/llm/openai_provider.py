"""OpenAI adapter: Responses API with structured outputs (`responses.parse` + a Pydantic model).

- The system prompt goes in `instructions`, first in every request, so OpenAI's automatic
  prompt caching can reuse it; the lifter-specific message comes last.
- `store=False`: replies aren't kept on OpenAI's side for later retrieval.
- Errors are sorted into InvalidOutput (retry with feedback) and LLMUnavailable (use the baseline).
  A Pydantic ValidationError from parsing passes through; refine.py treats it as feedback too.
"""

import logging
import time
from typing import Any, TypeVar

import openai
from openai import OpenAI
from pydantic import BaseModel

from app.llm.base import InvalidOutput, LLMUnavailable, LLMUsage

T = TypeVar("T", bound=BaseModel)

log = logging.getLogger(__name__)


class OpenAIProvider:
    def __init__(self, *, api_key: str, model: str, effort: str = "medium", timeout_s: float = 60.0,
                 max_output_tokens: int = 16000, client: Any = None):
        # No SDK retries: the review's time budget decides how long we wait (see ai_review.py).
        self.client = client or OpenAI(api_key=api_key, timeout=timeout_s, max_retries=0)
        self.timeout_s = timeout_s
        self.model = model
        self.effort = effort
        self.max_output_tokens = max_output_tokens
        self.name = f"openai:{model}"
        self.usage = LLMUsage()

    def generate(self, *, system: str, user: str, schema: type[T], timeout_s: float | None = None) -> T:
        start = time.monotonic()
        try:
            response = self.client.responses.parse(
                model=self.model,
                instructions=system,
                input=user,
                text_format=schema,
                reasoning={"effort": self.effort},
                max_output_tokens=self.max_output_tokens,
                store=False,
                timeout=min(timeout_s, self.timeout_s) if timeout_s is not None else self.timeout_s,
            )
        except openai.APITimeoutError as err:
            raise LLMUnavailable("OpenAI request timed out") from err
        except (openai.AuthenticationError, openai.PermissionDeniedError) as err:
            raise LLMUnavailable("OpenAI rejected the credentials (check OPENAI_API_KEY)") from err
        except openai.RateLimitError as err:
            raise LLMUnavailable("OpenAI rate limit or quota reached") from err
        except openai.BadRequestError as err:      # our request or schema is wrong: a bug, not the AI's mistake
            raise LLMUnavailable(f"OpenAI rejected the request: {err.message}") from err
        except (openai.APIConnectionError, openai.APIStatusError) as err:
            raise LLMUnavailable(f"OpenAI unavailable: {type(err).__name__}") from err
        finally:
            elapsed = time.monotonic() - start
            self.usage.seconds += elapsed
            self.usage.per_call_seconds.append(elapsed)

        self._count(response)
        if getattr(response, "status", None) == "incomplete":
            reason = getattr(getattr(response, "incomplete_details", None), "reason", None)
            if reason == "max_output_tokens":
                raise InvalidOutput("your reply was cut off before it finished; use fewer edits and "
                                    "shorter reasons")
            raise LLMUnavailable(f"OpenAI reply incomplete ({reason})")
        for item in getattr(response, "output", []) or []:
            for part in getattr(item, "content", None) or []:
                if getattr(part, "type", None) == "refusal":
                    raise LLMUnavailable("the model declined to answer")
        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            raise InvalidOutput("no JSON reply matching the schema was returned")
        return parsed

    def _count(self, response: Any) -> None:
        self.usage.calls += 1
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        self.usage.input_tokens += usage.input_tokens or 0
        self.usage.output_tokens += usage.output_tokens or 0
        self.usage.cached_tokens += getattr(usage.input_tokens_details, "cached_tokens", 0) or 0
        self.usage.reasoning_tokens += getattr(usage.output_tokens_details, "reasoning_tokens", 0) or 0
