import itertools

import pytest

from app.demo import build_demo
from app.exercises import get_exercise
from app.guards import guard_week, session_muscle_sets, weekly_muscle_sets
from app.rules import (
    MAX_SESSION_SETS_PER_MUSCLE,
    VOLUME_TARGET_MUSCLES,
    goal_rules,
    rules_for_exercise,
    target_rpe_range,
)
from app.schemas import LiftEstimate, Profile
from app.templates import SPLITS, TemplateError, _session_minutes, build_template, starting_load
from app.training import compute_metrics

FULL_GYM = ["barbell", "dumbbell", "machine", "cable", "bodyweight", "kettlebell"]
GOALS = ["strength", "hypertrophy", "general"]


def profile(**kw) -> Profile:
    return Profile(**{"goal": "hypertrophy", "experience": "intermediate", "days_per_week": 4,
                      "session_minutes": 90, "equipment": FULL_GYM, **kw})


def find(week, exercise_id):
    return next(e for s in week.sessions for e in s.exercises if e.exercise_id == exercise_id)


EQUIPMENT_SETS = [["bodyweight"], ["dumbbell", "bodyweight"], ["barbell", "bodyweight"],
                  ["machine", "cable", "bodyweight"], ["kettlebell", "bodyweight"], FULL_GYM]


@pytest.mark.parametrize("equipment,days,minutes", list(itertools.product(
    EQUIPMENT_SETS, SPLITS, [30, 45, 60, 75, 90, 120])))
def test_any_profile_gets_a_program_or_a_clear_reason(equipment, days, minutes):
    """Reviewer finding: bodyweight-only profiles crashed with a 500 (an empty session)."""
    try:
        program, _, _ = build_template(profile(equipment=equipment, days_per_week=days, session_minutes=minutes))
    except TemplateError as err:
        assert "equipment" in str(err)
        return
    assert all(s.exercises for w in program.weeks for s in w.sessions)


def test_bodyweight_only_names_the_day_it_cannot_fill():
    with pytest.raises(TemplateError, match="Lower A"):
        build_template(profile(equipment=["bodyweight"], days_per_week=4))


@pytest.mark.parametrize("goal,days", list(itertools.product(GOALS, SPLITS)))
def test_every_template_passes_the_guards(goal, days):
    program, issues, _ = build_template(profile(goal=goal, days_per_week=days))
    assert issues == []
    assert len(program.weeks) == 5 and program.weeks[-1].is_deload
    assert all(len(w.sessions) == days for w in program.weeks)


@pytest.mark.parametrize("goal,days", list(itertools.product(GOALS, [3, 4, 5, 6])))
def test_major_muscles_start_at_target_volume(goal, days):
    """Reviewer M1: with enough time, every major muscle starts at the goal's starting volume."""
    program, _, shortfalls = build_template(profile(goal=goal, days_per_week=days, session_minutes=75))
    assert shortfalls == []
    start = goal_rules(goal).volume.start.low
    week1 = weekly_muscle_sets(program.weeks[0])
    assert all(week1.get(m, 0) >= start for m in VOLUME_TARGET_MUSCLES)


@pytest.mark.parametrize("goal", GOALS)
def test_volume_ramps_within_limits(goal):
    program, _, _ = build_template(profile(goal=goal, session_minutes=75))
    volume = goal_rules(goal).volume
    hard = [weekly_muscle_sets(w) for w in program.weeks if not w.is_deload]
    for before, after in zip(hard, hard[1:]):
        for m in VOLUME_TARGET_MUSCLES:
            assert 0 <= after.get(m, 0) - before.get(m, 0) <= volume.ramp.high
    assert all(v <= volume.max for week in hard for v in week.values())
    if volume.ramp.low > 0:
        assert sum(hard[-1].values()) > sum(hard[0].values())          # hypertrophy volume actually ramps


@pytest.mark.parametrize("goal,days", list(itertools.product(GOALS, [3, 4, 5, 6])))
def test_no_muscle_gets_more_than_10_sets_in_one_session(goal, days):
    """Per-session ceiling (~10 sets/muscle/session, Schoenfeld; Remmert/Pelland meta-regression)."""
    program, _, _ = build_template(profile(goal=goal, days_per_week=days, session_minutes=90))
    for week in program.weeks:
        for s in week.sessions:
            assert max(session_muscle_sets(s).values()) <= MAX_SESSION_SETS_PER_MUSCLE


def test_front_delts_get_no_direct_volume_padding():
    """Front delts are covered by pressing; the builder must not add shoulder presses just for them."""
    program, _, _ = build_template(profile(session_minutes=90))
    added = {e.exercise_id for w in program.weeks for s in w.sessions for e in s.exercises if e.slot_id.startswith("x_")}
    assert "db_shoulder_press" not in added


def test_sessions_respect_time_budget():
    program, _, _ = build_template(profile(session_minutes=60))
    assert all(_session_minutes(s.exercises) <= 60 for w in program.weeks for s in w.sessions)


def test_short_schedule_reports_shortfalls_honestly():
    _, _, shortfalls = build_template(profile(days_per_week=2, session_minutes=45))
    assert shortfalls and all("weekly sets" in s for s in shortfalls)


def test_added_accessories_go_on_days_that_train_the_muscle():
    """With room on upper days, presses go there. (When upper days are full, extra work may
    spill onto the least busy day: that fallback is intended.)"""
    program, _, _ = build_template(profile(session_minutes=90))
    for week in program.weeks:
        for s in week.sessions:
            if s.focus.startswith("Lower"):
                assert not any(e.exercise_id in {"incline_db_press", "db_bench_press"} for e in s.exercises)


@pytest.mark.parametrize("goal", GOALS)
def test_prescriptions_follow_the_rules(goal):
    program, _, _ = build_template(profile(goal=goal))
    for session in program.weeks[0].sessions:
        for ex in session.exercises:
            info = get_exercise(ex.exercise_id)
            rules = rules_for_exercise(goal, info)
            rpe = target_rpe_range(rules)
            assert rpe.low <= ex.target_rpe <= rpe.high
            assert 1 <= ex.sets <= 5
            if not info.timed:
                assert rules.reps_limit.low <= ex.reps <= ex.reps_max <= rules.reps_limit.high


def test_strength_main_lifts_use_low_reps():
    program, _, _ = build_template(profile(goal="strength"))
    bench = find(program.weeks[0], "bench_press")
    assert (bench.reps, bench.reps_max) == (3, 5)


def test_hypertrophy_main_lifts_use_moderate_reps():
    program, _, _ = build_template(profile(goal="hypertrophy"))
    bench = find(program.weeks[0], "bench_press")
    assert (bench.reps, bench.reps_max) == (6, 10)


def test_equipment_respected():
    program, issues, _ = build_template(profile(equipment=["dumbbell"]))
    assert issues == []
    for s in program.weeks[0].sessions:
        for ex in s.exercises:
            assert get_exercise(ex.exercise_id).equipment in {"dumbbell", "bodyweight"}


def test_no_duplicate_exercises_in_a_session():
    program, _, _ = build_template(profile(days_per_week=2))
    for w in program.weeks:
        for s in w.sessions:
            ids = [e.exercise_id for e in s.exercises]
            assert len(ids) == len(set(ids))


def test_short_sessions_trim_accessories_first():
    long_program, _, _ = build_template(profile(session_minutes=120))
    short_program, _, _ = build_template(profile(session_minutes=30))
    long_s, short_s = long_program.weeks[0].sessions[0], short_program.weeks[0].sessions[0]
    assert len(short_s.exercises) < len(long_s.exercises)
    assert short_s.exercises[0].exercise_id == long_s.exercises[0].exercise_id   # main lift kept


def test_deload_week():
    program, _, _ = build_template(profile(goal="strength", lift_estimates=[
        LiftEstimate(exercise_id="bench_press", weight=225, reps=5)]))
    hard, deload = program.weeks[0], program.weeks[-1]
    assert find(deload, "bench_press").sets < find(hard, "bench_press").sets
    assert find(deload, "bench_press").target_rpe == find(hard, "bench_press").target_rpe - 2   # +2 RIR
    assert find(deload, "bench_press").load < find(hard, "bench_press").load                    # -10% load


def test_block_number():
    program, _, _ = build_template(profile(), block=3)
    assert program.block == 3 and program.adjusted_through == 0


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
        program, _, _ = build_template(profile())
        result = guard_week(program.weeks[0], "hypertrophy", "lb")
        assert result.violations == []

    def test_unknown_load_restored_when_history_exists(self):
        program, _, _ = build_template(profile(lift_estimates=[
            LiftEstimate(exercise_id="bench_press", weight=225, reps=5)]))
        prev = program.weeks[0]
        proposed = prev.model_copy(deep=True, update={"week": 2})
        find(proposed, "bench_press").load = None
        result = guard_week(proposed, "hypertrophy", "lb", previous=prev)
        assert find(result.week, "bench_press").load is not None
        assert any("load missing" in n for n in result.notes)


class TestDemo:
    def test_demo_is_consistent_with_the_engine(self):
        demo = build_demo()
        m = compute_metrics(demo.profile.goal, demo.program, demo.logs)
        steps = {s.next_step for l in m.lifts for s in l.slots}
        assert {"add_load", "add_reps"} <= steps
        assert "reduce" not in steps
        assert not m.deload.recommended
        assert not [v for v in m.muscle_volume if v.week == 2 and v.status == "below"]

    def test_demo_loads_come_from_the_engine(self):
        demo = build_demo()
        rx = lambda w, slot: next(e for s in demo.program.weeks[w - 1].sessions
                                  for e in s.exercises if e.slot_id == slot)
        assert rx(1, "squat").load == 275 and rx(2, "squat").load == 280      # top of 3-5 -> +2.5%, rounded down
        assert rx(1, "deadlift").load == 335 and rx(2, "deadlift").load == 340
        assert rx(2, "lateral").load == 20 and rx(3, "lateral").load == 25    # built to 18 reps (overshoot), then +5
        assert rx(3, "row").load == rx(1, "row").load                          # still adding reps
        assert demo.program.adjusted_through == 2

    def test_demo_has_warmups_and_session_in_progress(self):
        demo = build_demo()
        assert any(l.set_type == "warmup" for l in demo.logs)
        week3 = [l for l in demo.logs if l.week == 3 and l.set_type == "working"]
        assert {l.exercise_id for l in week3} == {"bench_press"} and len(week3) == 2
        m = compute_metrics(demo.profile.goal, demo.program, demo.logs)
        bench = next(s for l in m.lifts if l.exercise_id == "bench_press" for s in l.slots)
        assert bench.in_progress and bench.next_step_reason.startswith("This session:")
