"""The eval script works end to end with the scripted model (no key, no cost)."""

import json

from app.llm.fake import FakeLLM
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
