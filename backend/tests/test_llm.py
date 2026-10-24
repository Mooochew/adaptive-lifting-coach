"""Model adapters: the fake used in tests/e2e, and the OpenAI adapter against a stubbed SDK client."""

from types import SimpleNamespace

import httpx2
import openai
import pytest
from pydantic import BaseModel, ValidationError

from app.config import Settings
from app.llm import build_llm
from app.llm.base import InvalidOutput, LLMUnavailable
from app.llm.fake import FakeLLM
from app.llm.openai_provider import OpenAIProvider


class Answer(BaseModel):
    text: str


REQUEST = httpx2.Request("POST", "https://api.openai.com/v1/responses")


def status_error(cls, code):
    return cls("boom", response=httpx2.Response(code, request=REQUEST), body=None)


def ok_response(parsed=None, status="completed", output=(), usage=None, incomplete=None):
    usage = usage or SimpleNamespace(input_tokens=1200, output_tokens=300,
                                     input_tokens_details=SimpleNamespace(cached_tokens=1000),
                                     output_tokens_details=SimpleNamespace(reasoning_tokens=200))
    return SimpleNamespace(status=status, output=list(output), output_parsed=parsed, usage=usage,
                           incomplete_details=incomplete)


class StubResponses:
    def __init__(self, result):
        self.result = result
        self.kwargs = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def provider(result) -> OpenAIProvider:
    client = SimpleNamespace(responses=StubResponses(result))
    return OpenAIProvider(api_key="unused", model="gpt-6.1-sol", effort="medium", client=client)


class TestOpenAIProvider:
    def test_request_shape(self):
        p = provider(ok_response(Answer(text="hi")))
        assert p.generate(system="SYS", user="USER", schema=Answer).text == "hi"
        kw = p.client.responses.kwargs
        assert kw["instructions"] == "SYS" and kw["input"] == "USER"          # system first: cacheable prefix
        assert kw["text_format"] is Answer and kw["model"] == "gpt-6.1-sol"
        assert kw["reasoning"] == {"effort": "medium"} and kw["store"] is False

    def test_usage_is_counted(self):
        p = provider(ok_response(Answer(text="hi")))
        p.generate(system="s", user="u", schema=Answer)
        u = p.usage
        assert (u.calls, u.input_tokens, u.cached_tokens, u.output_tokens, u.reasoning_tokens) == (1, 1200, 1000, 300, 200)

    @pytest.mark.parametrize("error, says", [
        (openai.APITimeoutError(request=REQUEST), "timed out"),
        (openai.APIConnectionError(request=REQUEST), "unavailable"),
        (status_error(openai.RateLimitError, 429), "rate limit"),
        (status_error(openai.AuthenticationError, 401), "OPENAI_API_KEY"),
        (status_error(openai.InternalServerError, 500), "unavailable"),
        (status_error(openai.BadRequestError, 400), "rejected the request"),
    ])
    def test_outages_and_bad_requests_fall_back(self, error, says):
        with pytest.raises(LLMUnavailable, match=says):
            provider(error).generate(system="s", user="u", schema=Answer)

    @pytest.mark.parametrize("body, says", [
        ({"type": "insufficient_quota", "code": "credit_balance_exhausted"}, "no credits.*credit_balance_exhausted"),
        ({"type": "insufficient_quota", "code": "insufficient_quota"}, "no credits"),
        ({"type": "requests", "code": "rate_limit_exceeded"}, "rate limit.*rate_limit_exceeded"),
    ])
    def test_billing_and_rate_limits_are_told_apart(self, body, says):
        err = openai.RateLimitError("boom", response=httpx2.Response(429, request=REQUEST), body=body)
        with pytest.raises(LLMUnavailable, match=says):
            provider(err).generate(system="s", user="u", schema=Answer)

    def test_refusal_falls_back(self):
        refusal = SimpleNamespace(type="message", content=[SimpleNamespace(type="refusal", refusal="no")])
        with pytest.raises(LLMUnavailable, match="declined"):
            provider(ok_response(None, output=[refusal])).generate(system="s", user="u", schema=Answer)

    def test_cut_off_reply_is_feedback(self):
        cut = ok_response(None, status="incomplete", incomplete=SimpleNamespace(reason="max_output_tokens"))
        with pytest.raises(InvalidOutput, match="cut off"):
            provider(cut).generate(system="s", user="u", schema=Answer)

    def test_missing_parsed_output_is_feedback(self):
        with pytest.raises(InvalidOutput):
            provider(ok_response(None)).generate(system="s", user="u", schema=Answer)

    def test_schema_mismatch_passes_through_as_validation_error(self):
        try:
            Answer.model_validate({})
        except ValidationError as err:
            mismatch = err
        with pytest.raises(ValidationError):
            provider(mismatch).generate(system="s", user="u", schema=Answer)


class TestFakeLLM:
    def test_scripted_then_default(self):
        fake = FakeLLM({"text": "first"}, ValueError("bad"))
        assert fake.generate(system="s", user="u", schema=Answer).text == "first"
        with pytest.raises(ValueError):
            fake.generate(system="s", user="u", schema=Answer)
        assert fake.calls[1] == {"system": "s", "user": "u", "timeout_s": None} and fake.usage.calls == 2

    def test_malformed_dict_raises_like_a_bad_reply(self):
        with pytest.raises(ValidationError):
            FakeLLM({"nope": 1}).generate(system="s", user="u", schema=Answer)


class TestBuildLLM:
    def test_off_by_default_and_without_a_key(self):
        assert build_llm(Settings(_env_file=None)) is None
        assert build_llm(Settings(_env_file=None, llm_provider="openai")) is None
        assert build_llm(Settings(_env_file=None, llm_provider="openai", openai_api_key="  ")) is None

    def test_openai_with_a_key(self):
        llm = build_llm(Settings(_env_file=None, llm_provider="openai", openai_api_key="sk-test",
                                 llm_model="gpt-6.1-sol"))
        assert isinstance(llm, OpenAIProvider) and llm.name == "openai:gpt-6.1-sol"

    def test_fake(self):
        assert isinstance(build_llm(Settings(_env_file=None, llm_provider="fake")), FakeLLM)

    def test_key_is_secret(self):
        settings = Settings(_env_file=None, openai_api_key="sk-very-secret")
        assert "sk-very-secret" not in repr(settings) and "sk-very-secret" not in str(settings.model_dump())


class TestLimitsPassThrough:
    def test_timeout_effort_and_output_cap_reach_the_sdk(self):
        p = provider(ok_response(Answer(text="hi")))
        p.max_output_tokens, p.effort = 8000, "high"
        p.generate(system="s", user="u", schema=Answer, timeout_s=42.0)
        kw = p.client.responses.kwargs
        assert kw["timeout"] == 42.0 and kw["max_output_tokens"] == 8000 and kw["reasoning"] == {"effort": "high"}

    def test_build_llm_uses_the_settings(self):
        llm = build_llm(Settings(_env_file=None, llm_provider="openai", openai_api_key="sk-test", llm_model="m",
                                 llm_effort="low", llm_timeout_s=12, llm_max_output_tokens=3000))
        assert (llm.model, llm.effort, llm.timeout_s, llm.max_output_tokens) == ("m", "low", 12, 3000)

    def test_sdk_retries_are_off_so_the_time_budget_holds(self):
        llm = build_llm(Settings(_env_file=None, llm_provider="openai", openai_api_key="sk-test"))
        assert llm.client.max_retries == 0

    def test_defaults_bound_the_cost(self):
        s = Settings(_env_file=None)
        assert s.llm_max_output_tokens <= 8000 and s.llm_review_deadline_s <= 90
