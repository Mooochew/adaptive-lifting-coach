"""POST /program/review: the AI review over HTTP, with rate limits. Uses the fake model (no network)."""

import pytest
from fastapi.testclient import TestClient

from app.llm.base import LLMUnavailable
from app.llm.fake import FakeLLM
from app.main import app
from app.rate_limit import RateLimiter
from app.routes.program import get_limiter, get_llm, get_review_slots

client = TestClient(app)

PROFILE = {"goal": "hypertrophy", "experience": "intermediate", "days_per_week": 4, "session_minutes": 90,
           "equipment": ["barbell", "dumbbell", "machine", "cable", "kettlebell"],
           "injuries": "left shoulder hurts on overhead pressing"}

SWAP = {"kind": "swap", "day": 3, "slot_id": "ohp", "new_exercise_id": "db_shoulder_press", "muscle": None,
        "sets_change": None, "reps": None, "reps_max": None, "target_rpe": None, "weekly_sets": None,
        "for_injury": True, "reason": "Overhead pressing bothers your shoulder."}


@pytest.fixture
def use(request):
    """use(llm, limiter=None): override the route's dependencies for one test."""
    def apply(llm, limiter=None):
        app.dependency_overrides[get_llm] = lambda: llm
        app.dependency_overrides[get_limiter] = lambda: limiter or RateLimiter(per_window=100, window_s=60, per_day=1000)
    yield apply
    app.dependency_overrides.clear()


def post(profile=PROFILE, block=1):
    return client.post("/program/review", json={"profile": profile, "block": block})


def test_ai_review_applied(use):
    use(FakeLLM({"summary": "One shoulder-friendly swap.", "edits": [SWAP]}))
    res = post()
    assert res.status_code == 200
    data = res.json()
    assert data["source"] == "ai" and data["summary"] == "One shoulder-friendly swap."
    assert data["edits"][0]["change"] == "Standing Overhead Press → Seated Dumbbell Shoulder Press"
    assert data["edits"][0]["reason"] == "Overhead pressing bothers your shoulder." and data["fallback_reason"] is None
    day3 = data["program"]["weeks"][0]["sessions"][2]
    assert any(e["exercise_id"] == "db_shoulder_press" for e in day3["exercises"])
    assert set(data) >= {"program", "notes", "volume_shortfalls", "source", "summary", "edits", "fallback_reason"}


def test_ai_off_returns_the_standard_plan(use):
    use(None)
    data = post().json()
    assert data["source"] == "baseline" and data["edits"] == [] and "isn't set up" in data["fallback_reason"]
    assert data["program"] == client.post("/program/template", json={"profile": PROFILE}).json()["program"]


def test_outage_details_never_reach_the_response(use):
    use(FakeLLM(LLMUnavailable("OpenAI rejected the credentials (check OPENAI_API_KEY)")))
    res = post()
    assert res.status_code == 200 and res.json()["source"] == "baseline"
    assert "OPENAI_API_KEY" not in res.text and "credentials" not in res.text


def test_rate_limit_per_client_then_standard_plan(use):
    llm = FakeLLM()
    use(llm, RateLimiter(per_window=2, window_s=600, per_day=100))
    assert [post().json()["source"] for _ in range(3)] == ["ai", "ai", "baseline"]
    assert len(llm.calls) == 2
    assert "limit" in post().json()["fallback_reason"]


def test_daily_cap_across_clients(use):
    limiter = RateLimiter(per_window=100, window_s=600, per_day=2)
    use(FakeLLM(), limiter)
    assert limiter.allow("1.1.1.1") and limiter.allow("2.2.2.2") and not limiter.allow("3.3.3.3")


def test_unbuildable_profile_is_a_readable_422(use):
    use(FakeLLM())
    res = post({**PROFILE, "equipment": ["bodyweight"], "days_per_week": 6})
    assert res.status_code == 422 and "equipment" in res.json()["detail"]


def test_training_background_is_validated(use):
    use(FakeLLM())
    assert post({**PROFILE, "training_background": "x" * 501}).status_code == 422


class TestRateLimiter:
    def test_window_slides(self):
        now = [0.0]
        limiter = RateLimiter(per_window=2, window_s=10, per_day=100, clock=lambda: now[0])
        assert limiter.allow("a") and limiter.allow("a") and not limiter.allow("a")
        now[0] = 11
        assert limiter.allow("a")

    def test_clients_are_separate(self):
        limiter = RateLimiter(per_window=1, window_s=10, per_day=100)
        assert limiter.allow("a") and limiter.allow("b") and not limiter.allow("a")

    def test_day_resets(self):
        now = [0.0]
        limiter = RateLimiter(per_window=100, window_s=1, per_day=1, clock=lambda: now[0])
        assert limiter.allow("a") and not limiter.allow("b")
        now[0] = 86_401
        assert limiter.allow("b")

    def test_refused_calls_do_not_use_up_the_budget(self):
        limiter = RateLimiter(per_window=1, window_s=10, per_day=1)
        assert limiter.allow("a") and not limiter.allow("a") and not limiter.allow("b")
        assert limiter.used_today == 1


def test_too_many_reviews_at_once_get_the_standard_plan(use):
    import threading
    slots = threading.BoundedSemaphore(1)
    slots.acquire()                                   # one review already in progress
    use(FakeLLM())
    app.dependency_overrides[get_review_slots] = lambda: slots
    data = post().json()
    assert data["source"] == "baseline" and "busy" in data["fallback_reason"]


def test_ai_off_does_not_use_up_the_limits(use):
    limiter = RateLimiter(per_window=1, window_s=600, per_day=1)
    use(None, limiter)
    post(), post()
    assert limiter.used_today == 0


def test_standard_plan_keeps_guard_violations_like_template(use):
    use(None)
    review = post().json()
    template = client.post("/program/template", json={"profile": PROFILE}).json()
    assert review["notes"] == template["notes"]
