import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas import PrescribedExercise, Session
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
    res = client.post("/program/template", json=profile)
    assert res.status_code == 200
    data = res.json()
    assert data["notes"] == [] and len(data["program"]["weeks"]) == 5


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
