"""A scripted stand-in for a real model: tests, e2e runs and demos without a key or cost."""

from collections.abc import Callable
from typing import TypeVar

from pydantic import BaseModel

from app.llm.base import LLMUsage

T = TypeVar("T", bound=BaseModel)

Reply = BaseModel | dict | Exception | Callable[[str, str], BaseModel | dict]


class FakeLLM:
    """Returns (or raises) one scripted reply per call, then `default` forever.

    Replies may be a model, a dict (validated against the requested schema, so a malformed
    dict raises a ValidationError just like a bad real reply), an exception, or a function of
    (system, user). Every call's prompts are recorded in `calls`.
    """

    name = "fake"

    def __init__(self, *replies: Reply, default: Reply | None = None):
        self.replies = list(replies)
        self.default = default
        self.calls: list[dict[str, object]] = []
        self.usage = LLMUsage()

    def generate(self, *, system: str, user: str, schema: type[T], timeout_s: float | None = None) -> T:
        self.calls.append({"system": system, "user": user, "timeout_s": timeout_s})
        self.usage.calls += 1
        reply = self.replies.pop(0) if self.replies else self.default
        if reply is None:
            reply = {"summary": "This plan already fits you well, so I'd keep it as it is.", "edits": []}
        if callable(reply) and not isinstance(reply, BaseModel):
            reply = reply(system, user)
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, schema) else schema.model_validate(reply)
