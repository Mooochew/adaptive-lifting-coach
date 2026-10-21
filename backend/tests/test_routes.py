import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import MAX_BODY_BYTES, app
from app.schemas import MAX_LOGS, LiftEstimate, PrescribedExercise, Profile, Session, SetLog
from tests.conftest import bench_rx, bench_sets, make_program

client = TestClient(app)


def test_health():
    assert client.get("/health").json()["ok"] is True


def test_exercises_listed():
    ids = {e["id"] for e in client.get("/exercises").json()}
    assert {"bench_press", "back_squat", "calf_raise"} <= ids


def test_rules_listed():
    data = client.get("/rules").json()
    assert data["goals"]["strength"]["main"]["load_step_pct"] == 0.025
    assert data["goals"]["strength"]["compound"]["load_step_pct"] == 0.0   # min step only
    assert data["shared"]["min_load_step"] == {"lb": 5.0, "kg": 2.5}
    assert data["goals"]["hypertrophy"]["volume"]["max"] == 20
    assert data["shared"]["rpe_tolerance"] == 0.5


def test_openapi_types_are_not_any():
    schema = client.get("/openapi.json").json()["paths"]
    for path in ("/exercises", "/rules", "/health"):
        assert schema[path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]


def test_metrics_endpoint():
    body = {
        "goal": "strength",
        "program": make_program().model_dump(),
        "logs": [log.model_dump() for log in bench_sets(1, 185, 5, 9)],
    }
    res = client.post("/metrics", json=body)
    assert res.status_code == 200
    bench = res.json()["lifts"][0]
    assert bench["avg_rpe_gap"] == 1.0
    assert bench["slots"][0]["next_step"] == "hold"


def test_metrics_rejects_invalid_rpe():
    log = bench_sets(1, 185, 5, 9)[0].model_dump()
    log["rpe"] = 14
    res = client.post("/metrics", json={"goal": "strength", "program": make_program().model_dump(), "logs": [log]})
    assert res.status_code == 422


def test_timed_hold_accepted():
    log = {"week": 1, "day": 1, "exercise_id": "plank", "set_number": 1, "weight": 0, "reps": 90}
    res = client.post("/metrics", json={"goal": "general", "program": make_program().model_dump(), "logs": [log]})
    assert res.status_code == 200


class TestSchemaValidation:
    def test_reps_max_below_reps_rejected(self):
        with pytest.raises(ValidationError):
            bench_rx(reps=12, reps_max=8)

    def test_duplicate_slot_ids_rejected(self):
        with pytest.raises(ValidationError):
            Session(day=1, focus="x", exercises=[bench_rx(), bench_rx()])

    def test_bad_slot_id_rejected(self):
        with pytest.raises(ValidationError):
            bench_rx(slot_id="Bench Press!")


def test_template_endpoint():
    profile = {"goal": "strength", "experience": "beginner", "days_per_week": 3, "session_minutes": 60,
               "equipment": ["barbell", "dumbbell"]}
    res = client.post("/program/template", json={"profile": profile, "block": 2})
    assert res.status_code == 200
    data = res.json()
    assert data["notes"] == [] and len(data["program"]["weeks"]) == 5
    assert data["program"]["block"] == 2 and "volume_shortfalls" in data


def test_advance_endpoint_is_idempotent():
    demo = client.get("/demo").json()
    body = {"goal": demo["profile"]["goal"], "program": demo["program"], "logs": demo["logs"]}
    res = client.post("/program/advance", json=body)
    assert res.status_code == 200
    assert res.json()["changes"] == []          # the demo is already advanced through week 2


def test_demo_endpoint_feeds_metrics():
    demo = client.get("/demo").json()
    res = client.post("/metrics", json={"goal": demo["profile"]["goal"], "program": demo["program"],
                                        "logs": demo["logs"]})
    assert res.status_code == 200 and res.json()["lifts"]


def test_committed_openapi_is_current():
    """The frontend's types are generated from backend/openapi.json; it must match the code.

    If this fails: python scripts/export_openapi.py, then npm run gen:types in frontend/.
    """
    path = Path(__file__).resolve().parent.parent / "openapi.json"
    assert json.loads(path.read_text(encoding="utf-8")) == app.openapi()


def test_cors_allows_frontend_origin():
    res = client.options(
        "/metrics",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"},
    )
    assert res.headers.get("access-control-allow-origin") == "http://localhost:3000"


class TestInputLimits:
    """Bad or oversized input gets a clear 4xx, never a 500 or silent nonsense."""

    def metrics_body(self, **log_changes) -> dict:
        log = {**bench_sets(1, 185, 5, 8)[0].model_dump(), **log_changes}
        return {"goal": "strength", "program": make_program().model_dump(), "logs": [log]}

    def post_raw(self, path: str, text: str):
        return client.post(path, content=text, headers={"content-type": "application/json"})

    @pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity"])
    def test_non_finite_numbers_rejected(self, bad):
        """Reviewer finding: NaN used to crash the error response with a 500."""
        text = json.dumps(self.metrics_body(weight=123456.0)).replace("123456.0", bad)
        res = self.post_raw("/metrics", text)
        assert res.status_code == 422
        assert "finite" in res.text

    def test_absurd_weight_rejected(self):
        assert client.post("/metrics", json=self.metrics_body(weight=1e308)).status_code == 422

    def test_absurd_load_rejected(self):
        program = make_program().model_dump()
        program["weeks"][0]["sessions"][0]["exercises"][0]["load"] = 5000
        res = client.post("/metrics", json={"goal": "strength", "program": program, "logs": []})
        assert res.status_code == 422

    def test_unknown_exercise_in_logs_rejected(self):
        res = client.post("/metrics", json=self.metrics_body(exercise_id="bench"))
        assert res.status_code == 422 and "unknown exercise id 'bench'" in res.text

    def test_unknown_exercise_in_program_rejected(self):
        program = make_program().model_dump()
        program["weeks"][0]["sessions"][0]["exercises"][0]["exercise_id"] = "made_up_lift"
        res = client.post("/program/advance", json={"goal": "strength", "program": program, "logs": []})
        assert res.status_code == 422 and "made_up_lift" in res.text

    def test_unknown_exercise_in_profile_rejected(self):
        profile = {"goal": "strength", "experience": "beginner", "days_per_week": 3, "session_minutes": 60,
                   "equipment": ["barbell"], "lift_estimates": [{"exercise_id": "bench", "weight": 185, "reps": 5}]}
        assert client.post("/program/template", json={"profile": profile}).status_code == 422

    def test_too_many_logs_rejected(self):
        body = self.metrics_body()
        body["logs"] = body["logs"] * (MAX_LOGS + 1)
        res = client.post("/metrics", json=body)
        assert res.status_code == 422

    def test_oversized_body_rejected_before_parsing(self):
        res = self.post_raw("/metrics", " " * (MAX_BODY_BYTES + 1))
        assert res.status_code == 413

    def test_oversized_body_error_is_readable_by_the_frontend(self):
        res = client.post("/metrics", content=" " * (MAX_BODY_BYTES + 1),
                          headers={"content-type": "application/json", "Origin": "http://localhost:3000"})
        assert res.status_code == 413
        assert res.headers.get("access-control-allow-origin") == "http://localhost:3000"

    def test_long_text_rejected(self):
        assert client.post("/metrics", json=self.metrics_body(note="x" * 501)).status_code == 422

    def test_bodyweight_only_gets_a_readable_422_not_a_500(self):
        profile = {"goal": "hypertrophy", "experience": "beginner", "days_per_week": 6, "session_minutes": 60,
                   "equipment": ["bodyweight"]}
        res = client.post("/program/template", json={"profile": profile})
        assert res.status_code == 422
        assert isinstance(res.json()["detail"], str) and "equipment" in res.json()["detail"]

    @pytest.mark.parametrize("field, value", [("block", 1001), ("adjusted_through", 17)])
    def test_program_counters_are_bounded(self, field, value):
        """A huge block number would become Infinity in the browser and break every later request."""
        program = {**make_program().model_dump(), field: value}
        res = client.post("/metrics", json={"goal": "strength", "program": program, "logs": []})
        assert res.status_code == 422


class TestSchemaLimits:
    """Each cap checked by behaviour, not only by the OpenAPI snapshot."""

    @pytest.mark.parametrize("make", [
        lambda: Session(day=1, focus="x" * 61, exercises=[bench_rx()]),
        lambda: bench_rx(notes="x" * 501),
        lambda: bench_rx(load=2001),
        lambda: Session(day=1, focus="x", exercises=[bench_rx(slot_id=f"s{i}") for i in range(16)]),
        lambda: SetLog(week=1, day=1, slot_id="s" * 41, exercise_id="bench_press", set_number=1, weight=100, reps=5),
        lambda: SetLog(week=1, day=1, exercise_id="bench_press", set_number=1, weight=2001, reps=5),
        lambda: LiftEstimate(exercise_id="bench_press", weight=2001, reps=5),
        lambda: Profile(goal="strength", experience="beginner", days_per_week=3, session_minutes=60,
                        equipment=["barbell"], lift_estimates=[
                            LiftEstimate(exercise_id="bench_press", weight=100, reps=5)] * 21),
    ])
    def test_over_the_limit_is_rejected(self, make):
        with pytest.raises(ValidationError):
            make()

    def test_at_the_limit_is_accepted(self):
        Session(day=1, focus="x" * 60, exercises=[bench_rx(slot_id=f"s{i}", load=2000) for i in range(15)])
        SetLog(week=1, day=1, slot_id="s" * 40, exercise_id="bench_press", set_number=1, weight=2000, reps=5,
               note="x" * 500)
