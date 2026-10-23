"""The eval script works end to end with the scripted model (no key, no cost)."""

import io
import json
import sys

from app.llm.fake import FakeLLM
from app.schemas import Profile
from app.templates import build_week_one
from scripts.run_eval import CASES, check, main, run


def cases():
    return json.loads(CASES.read_text(encoding="utf-8"))["cases"]


def test_every_case_is_a_valid_profile_and_runs(tmp_path):
    path = main(["--fake", "--out", str(tmp_path)])
    text = path.read_text(encoding="utf-8")
    assert "Accepted on the first try: **10/10**" in text
    assert all(f"## {c['id']}" in text for c in cases())
    rows = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    assert len(rows) == 10 and {r["source"] for r in rows} == {"ai"}


def test_summary_prints_on_a_windows_console(tmp_path, monkeypatch):
    # A redirected stdout on Windows is cp1252, which has no ✓.
    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="cp1252"))
    main(["--fake", "--cases", "shoulder-overhead", "--out", str(tmp_path)])
    sys.stdout.flush()
    assert "Accepted on the first try" in raw.getvalue().decode("utf-8")


def test_checks_catch_an_unaddressed_injury_and_credit_a_fix():
    shoulder = next(c for c in cases() if c["id"] == "shoulder-overhead")
    untouched = run(FakeLLM(), [shoulder])[0]
    assert untouched["checks"] == {"addresses overhead_press": False, "at most 3 edits": True}

    swap = {"summary": "Shoulder-friendly press.", "edits": [{
        "kind": "swap", "day": 3, "slot_id": "ohp", "new_exercise_id": "db_shoulder_press", "muscle": None,
        "sets_change": None, "reps": None, "reps_max": None, "target_rpe": None, "weekly_sets": None,
        "for_injury": True, "reason": "Overhead pressing hurts your shoulder."}]}
    fixed = run(FakeLLM(swap), [shoulder])[0]
    assert fixed["passed"] and fixed["edits"][0]["change"].startswith("Standing Overhead Press")


def test_lower_volume_check():
    case = next(c for c in cases() if c["id"] == "experienced-low-volume")
    lower = {"summary": "Fewer, harder sets.", "edits": [{
        "kind": "volume", "day": None, "slot_id": None, "new_exercise_id": None, "muscle": "chest",
        "sets_change": None, "reps": None, "reps_max": None, "target_rpe": None, "weekly_sets": 7,
        "for_injury": False, "reason": "You grow best on low volume."}]}
    assert run(FakeLLM(lower), [case])[0]["checks"]["lowers some muscle's volume"]
    assert not run(FakeLLM(), [case])[0]["checks"]["lowers some muscle's volume"]


def test_edit_count_and_first_try_are_measured():
    case = next(c for c in cases() if c["id"] == "beginner-no-notes")       # max_edits: 1
    two = {"summary": "Two tweaks.", "edits": [
        {"kind": "effort", "day": 1, "slot_id": s, "target_rpe": 7, "new_exercise_id": None, "muscle": None,
         "sets_change": None, "reps": None, "reps_max": None, "weekly_sets": None, "for_injury": False,
         "reason": "Ease in."} for s in ("bench", "row")]}
    assert run(FakeLLM(two), [case])[0]["checks"] == {"at most 1 edits": False}
    bad = {"summary": "x", "edits": [{"kind": "sets", "day": 1, "slot_id": "nope", "sets_change": 1,
                                      "new_exercise_id": None, "muscle": None, "reps": None, "reps_max": None,
                                      "target_rpe": None, "weekly_sets": None, "for_injury": False, "reason": "x"}]}
    row = run(FakeLLM(bad), [case])[0]
    assert row["source"] == "ai" and row["attempts"] == 2 and row["first_try"] is False


def test_address_needs_every_copy_of_the_exercise():
    knee = next(c for c in cases() if c["id"] == "knee-deep-squat")
    profile_week = build_week_one(Profile.model_validate(knee["profile"]))[0]
    squat_slots = [(s.day, e.slot_id) for s in profile_week.sessions for e in s.exercises if e.exercise_id == "back_squat"]
    one = {"summary": "Box squats feel better.", "edits": [{
        "kind": "effort", "day": squat_slots[0][0], "slot_id": squat_slots[0][1], "target_rpe": 7,
        "new_exercise_id": None, "muscle": None, "sets_change": None, "reps": None, "reps_max": None,
        "weekly_sets": None, "for_injury": True, "reason": "Easier on the knee."}]}
    result = run(FakeLLM(one), [knee])[0]["checks"]["addresses back_squat"]
    assert result is (len(squat_slots) == 1)
