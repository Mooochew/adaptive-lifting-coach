"""Demo mode: replay recorded AI replies, record new ones from the live model (no network in tests)."""

import json

import pytest
from pydantic import BaseModel

from app.config import Settings
from app.llm import build_llm
from app.llm.base import LLMUnavailable
from app.llm.fake import FakeLLM
from app.llm.replay import ReplayLLM


class Answer(BaseModel):
    text: str


class Other(BaseModel):
    text: str


def test_records_a_live_reply_then_replays_it_without_the_model(tmp_path):
    live = FakeLLM({"text": "from the model"})
    first = ReplayLLM(tmp_path, live).generate(system="S", user="U", schema=Answer)
    assert first.text == "from the model" and len(live.calls) == 1
    [saved] = list(tmp_path.glob("*.json"))
    assert json.loads(saved.read_text(encoding="utf-8"))["reply"] == {"text": "from the model"}

    offline = ReplayLLM(tmp_path, None)                       # no network, no key
    assert offline.generate(system="S", user="U", schema=Answer).text == "from the model"
    assert offline.usage.calls == 0


def test_a_new_request_goes_live_and_without_a_model_is_unavailable(tmp_path):
    ReplayLLM(tmp_path, FakeLLM({"text": "a"})).generate(system="S", user="U", schema=Answer)
    with pytest.raises(LLMUnavailable, match="no recording"):
        ReplayLLM(tmp_path, None).generate(system="S", user="different lifter", schema=Answer)
    with pytest.raises(LLMUnavailable):                       # same prompts, another reply schema
        ReplayLLM(tmp_path, None).generate(system="S", user="U", schema=Other)


def test_a_failed_live_call_records_nothing(tmp_path):
    with pytest.raises(LLMUnavailable):
        ReplayLLM(tmp_path, FakeLLM(LLMUnavailable("down"))).generate(system="S", user="U", schema=Answer)
    assert list(tmp_path.glob("*.json")) == []


def test_an_unreadable_recording_goes_live_again(tmp_path):
    llm = ReplayLLM(tmp_path, FakeLLM({"text": "a"}, {"text": "b"}))
    llm.generate(system="S", user="U", schema=Answer)
    [saved] = list(tmp_path.glob("*.json"))
    saved.write_text("{broken", encoding="utf-8")
    assert llm.generate(system="S", user="U", schema=Answer).text == "b"


def test_recordings_are_per_model(tmp_path):
    ReplayLLM(tmp_path, FakeLLM({"text": "a"}), model="model-a").generate(system="S", user="U", schema=Answer)
    assert ReplayLLM(tmp_path, None, model="model-a").generate(system="S", user="U", schema=Answer).text == "a"
    with pytest.raises(LLMUnavailable):
        ReplayLLM(tmp_path, None, model="model-b").generate(system="S", user="U", schema=Answer)


def test_a_reply_that_cant_be_saved_is_still_returned(tmp_path):
    blocked = tmp_path / "a-file"
    blocked.write_text("not a folder", encoding="utf-8")
    assert ReplayLLM(blocked, FakeLLM({"text": "a"})).generate(system="S", user="U", schema=Answer).text == "a"


def test_replays_dont_count_as_model_calls(tmp_path):
    live = FakeLLM({"text": "a"})
    llm = ReplayLLM(tmp_path, live)
    llm.generate(system="S", user="U", schema=Answer)
    llm.generate(system="S", user="U", schema=Answer)
    assert live.usage.calls == 1 and llm.usage.calls == 1


def settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)          # never the developer's backend/.env


def test_demo_mode_wraps_the_configured_model(tmp_path):
    llm = build_llm(settings(llm_provider="fake", demo_mode=True, demo_dir=str(tmp_path)))
    assert isinstance(llm, ReplayLLM) and llm.name == "replay+fake" and llm.model == "gpt-6.1-sol"
    offline = build_llm(settings(llm_provider="", demo_mode=True, demo_dir=str(tmp_path)))
    assert isinstance(offline, ReplayLLM) and offline.live is None
    assert not isinstance(build_llm(settings(llm_provider="fake", demo_mode=False)), ReplayLLM)
