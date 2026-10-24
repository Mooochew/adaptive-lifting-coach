"""POST /coach/notes: the AI coach notes over HTTP, with rate limits. Uses the fake model (no network)."""

import pytest
from fastapi.testclient import TestClient

from app.demo import build_demo
from app.llm.base import LLMUnavailable
from app.llm.fake import FakeLLM
from app.main import app
from app.rate_limit import RateLimiter
from app.routes.coach import get_notes_limiter
from app.routes.program import get_llm

client = TestClient(app)
DEMO = build_demo()


@pytest.fixture
def use():
    def apply(llm, limiter=None):
        app.dependency_overrides[get_llm] = lambda: llm
        app.dependency_overrides[get_notes_limiter] = lambda: limiter or RateLimiter(per_window=100, window_s=60,
                                                                                     per_day=1000)
    yield apply
    app.dependency_overrides.clear()


def post(logs=None, changes=()):
    body = {"profile": DEMO.profile.model_dump(), "program": DEMO.program.model_dump(),
            "logs": [l.model_dump() for l in (DEMO.logs if logs is None else logs)], "changes": list(changes)}
    return client.post("/coach/notes", json=body)


def test_a_grounded_note(use):
    use(FakeLLM({"headline": "Bench reps are climbing.", "focus": "Hit 5 on every bench set.",
                 "points": [{"subject": "bench_press", "text": "You held 185 lb and added reps."}]}))
    res = post()
    assert res.status_code == 200
    data = res.json()
    assert data["source"] == "ai" and data["week"] == 3 and data["in_progress"] is True
    assert data["points"] == [{"subject": "bench_press", "label": "Barbell Bench Press",
                               "text": "You held 185 lb and added reps."}]
    assert data["reason"] is None and data["attempts"] == 1


def test_ai_off_and_nothing_logged(use):
    use(None)
    assert "isn't set up" in post().json()["reason"]
    use(FakeLLM())
    data = post(logs=[]).json()
    assert data["source"] == "none" and data["week"] is None and "Log a week" in data["reason"]


def test_outage_details_never_reach_the_response(use):
    use(FakeLLM(LLMUnavailable("OpenAI rejected the credentials (check OPENAI_API_KEY)")))
    data = post().json()
    assert data["source"] == "none" and "OPENAI" not in data["reason"]


def test_rate_limited(use):
    use(FakeLLM(), RateLimiter(per_window=1, window_s=600, per_day=100))
    assert post().json()["source"] == "ai"
    data = post().json()
    assert data["source"] == "none" and "limit" in data["reason"]


def test_change_lines_are_bounded(use):
    use(FakeLLM())
    assert post(changes=["x" * 201]).status_code == 422
    assert post(changes=["W3 D1 bench: 185 -> 190 lb"] * 61).status_code == 422


def test_busy_never_calls_the_model(use):
    import threading
    from app.routes.program import get_review_slots
    llm = FakeLLM()
    use(llm)
    slots = threading.BoundedSemaphore(1)
    slots.acquire()                                   # another AI call in progress
    app.dependency_overrides[get_review_slots] = lambda: slots
    data = post().json()
    assert data["source"] == "none" and "busy" in data["reason"] and data["retryable"] and llm.calls == []


def test_nothing_logged_uses_no_quota(use):
    use(FakeLLM(), RateLimiter(per_window=1, window_s=600, per_day=100))
    empty = post(logs=[]).json()
    assert "Log a week" in empty["reason"] and empty["retryable"] is False
    assert post().json()["source"] == "ai"            # the one allowed note is still there


def test_the_week_asked_about_and_the_time_budget(use):
    from app.config import get_settings
    llm = FakeLLM()
    use(llm)
    body = {"profile": DEMO.profile.model_dump(), "program": DEMO.program.model_dump(),
            "logs": [l.model_dump() for l in DEMO.logs], "week": 2}
    data = client.post("/coach/notes", json=body).json()
    assert data["week"] == 2 and data["in_progress"] is False
    assert llm.calls[0]["timeout_s"] == get_settings().llm_review_deadline_s
