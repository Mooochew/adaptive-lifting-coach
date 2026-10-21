import itertools

import pytest

from app.demo import build_demo
from app.exercises import get_exercise
from app.guards import guard_week
from app.rules import rules_for_exercise, target_rpe_range
from app.schemas import LiftEstimate, Profile
from app.templates import SPLITS, build_template, starting_load
from app.training import compute_metrics

FULL_GYM = ["barbell", "dumbbell", "machine", "cable", "bodyweight", "kettlebell"]


def profile(**kw) -> Profile:
    return Profile(**{"goal": "hypertrophy", "experience": "intermediate", "days_per_week": 4,
                      "session_minutes": 90, "equipment": FULL_GYM, **kw})


@pytest.mark.parametrize("goal,days", itertools.product(["strength", "hypertrophy", "general"], SPLITS))
def test_every_template_passes_the_guards(goal, days):
    program, issues = build_template(profile(goal=goal, days_per_week=days))
    assert issues == []
    assert len(program.weeks) == 5 and program.weeks[-1].is_deload
    assert all(len(w.sessions) == days for w in program.weeks)


@pytest.mark.parametrize("goal", ["strength", "hypertrophy", "general"])
def test_prescriptions_follow_the_rules(goal):
    program, _ = build_template(profile(goal=goal))
    for session in program.weeks[0].sessions:
        for ex in session.exercises:
            info = get_exercise(ex.exercise_id)
            rules = rules_for_exercise(goal, info)
            rpe = target_rpe_range(rules)
            assert rpe.low <= ex.target_rpe <= rpe.high
            if not info.timed:
                assert rules.reps_limit.low <= ex.reps <= ex.reps_max <= rules.reps_limit.high


def test_strength_main_lifts_use_low_reps():
    program, _ = build_template(profile(goal="strength"))
    bench = next(e for s in program.weeks[0].sessions for e in s.exercises if e.exercise_id == "bench_press")
    assert (bench.reps, bench.reps_max) == (3, 5)


def test_hypertrophy_main_lifts_use_moderate_reps():
    program, _ = build_template(profile(goal="hypertrophy"))
    bench = next(e for s in program.weeks[0].sessions for e in s.exercises if e.exercise_id == "bench_press")
    assert (bench.reps, bench.reps_max) == (6, 10)


def test_equipment_respected():
    program, issues = build_template(profile(equipment=["dumbbell"]))
    assert issues == []
    for s in program.weeks[0].sessions:
        for ex in s.exercises:
            assert get_exercise(ex.exercise_id).equipment in {"dumbbell", "bodyweight"}


def test_no_duplicate_exercises_in_a_session():
    program, _ = build_template(profile(days_per_week=2))
    for s in program.weeks[0].sessions:
        ids = [e.exercise_id for e in s.exercises]
        assert len(ids) == len(set(ids))


def test_short_sessions_trim_accessories_first():
    long_program, _ = build_template(profile(session_minutes=120))
    short_program, _ = build_template(profile(session_minutes=30))
    long_s, short_s = long_program.weeks[0].sessions[0], short_program.weeks[0].sessions[0]
    assert len(short_s.exercises) < len(long_s.exercises)
    assert short_s.exercises[0].exercise_id == long_s.exercises[0].exercise_id   # main lift kept


def test_deload_week():
    program, _ = build_template(profile(goal="strength", lift_estimates=[
        LiftEstimate(exercise_id="bench_press", weight=225, reps=5)]))
    hard, deload = program.weeks[0], program.weeks[-1]
    bench = lambda w: next(e for s in w.sessions for e in s.exercises if e.exercise_id == "bench_press")
    assert bench(deload).sets < bench(hard).sets
    assert bench(deload).target_rpe == bench(hard).target_rpe - 2      # strength: +2 RIR
    assert bench(deload).load < bench(hard).load                       # strength: -10% load


class TestStartingLoad:
    def test_from_estimate(self):
        # 225 x 5 -> e1RM 262.5; 3 reps @ RPE 8 = 5-rep effort -> 262.5 / 1.1667 = 225 -> 225
        p = profile(lift_estimates=[LiftEstimate(exercise_id="bench_press", weight=225, reps=5)])
        assert starting_load(p, get_exercise("bench_press"), 3, 8) == 225

    def test_rounds_down(self):
        # 200 x 8 -> e1RM 253.3; 6 reps @ RPE 8 = 8-rep effort -> 200.0 exactly -> 200
        # 4 reps @ RPE 8 = 6-rep effort -> 211.1 -> rounds DOWN to 210
        p = profile(lift_estimates=[LiftEstimate(exercise_id="bench_press", weight=200, reps=8)])
        assert starting_load(p, get_exercise("bench_press"), 6, 8) == 200
        assert starting_load(p, get_exercise("bench_press"), 4, 8) == 210

    def test_unknown_lift_is_none(self):
        assert starting_load(profile(), get_exercise("leg_press"), 8, 8) is None

    def test_bodyweight_is_zero(self):
        assert starting_load(profile(), get_exercise("pull_up"), 6, 8) == 0


class TestGuardsWithUnknownLoad:
    def test_unknown_load_allowed_for_new_lift(self):
        program, _ = build_template(profile())
        result = guard_week(program.weeks[0], "hypertrophy", "lb")
        assert result.violations == []

    def test_unknown_load_restored_when_history_exists(self):
        program, _ = build_template(profile(lift_estimates=[
            LiftEstimate(exercise_id="bench_press", weight=225, reps=5)]))
        prev = program.weeks[0]
        proposed = prev.model_copy(deep=True, update={"week": 2})
        bench = next(e for s in proposed.sessions for e in s.exercises if e.exercise_id == "bench_press")
        bench.load = None
        result = guard_week(proposed, "hypertrophy", "lb", previous=prev)
        restored = next(e for s in result.week.sessions for e in s.exercises if e.exercise_id == "bench_press")
        assert restored.load is not None and any("load missing" in n for n in result.notes)


class TestDemo:
    def test_demo_is_consistent_with_the_engine(self):
        demo = build_demo()
        m = compute_metrics(demo.profile.goal, demo.program, demo.logs)
        steps = {s.next_step for l in m.lifts for s in l.slots}
        assert {"add_load", "add_reps"} <= steps
        assert "reduce" not in steps
        assert not m.deload.recommended

    def test_demo_loads_follow_suggestions(self):
        demo = build_demo()
        rx = lambda w, slot: next(e for s in demo.program.weeks[w - 1].sessions
                                  for e in s.exercises if e.slot_id == slot)
        assert rx(1, "squat").load == 275 and rx(2, "squat").load == 280      # top of 3-5 -> +2.5%
        assert rx(2, "lateral").load == 20 and rx(3, "lateral").load == 25   # top of range -> +5 lb
        assert rx(3, "row").load == rx(1, "row").load                         # still adding reps

    def test_demo_has_warmups_and_session_in_progress(self):
        demo = build_demo()
        assert any(l.set_type == "warmup" for l in demo.logs)
        week3 = [l for l in demo.logs if l.week == 3 and l.set_type == "working"]
        assert {l.exercise_id for l in week3} == {"bench_press"} and len(week3) == 2
