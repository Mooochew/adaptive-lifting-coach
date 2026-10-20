import pytest

from app.guards import (
    UnknownExerciseError,
    clamp_load,
    ensure_known_exercises,
    guard_week,
    weekly_muscle_sets,
)
from app.schemas import PrescribedExercise, Session, Week
from tests.conftest import bench_rx, lateral_rx, make_program, squat_rx


def week_of(*exercises, day=1, n=2, deload=False) -> Week:
    return Week(week=n, is_deload=deload, sessions=[Session(day=day, focus="x", exercises=list(exercises))])


class TestClampLoad:
    def test_reasonable_increase_kept(self):
        assert clamp_load(185, 190, "lb") == 190

    def test_big_jump_capped(self):
        assert clamp_load(200, 240, "lb") == 210

    def test_light_lift_can_progress_one_min_step(self):
        assert clamp_load(60, 70, "lb") == 65

    def test_crash_capped(self):
        assert clamp_load(200, 100, "lb") == 160

    def test_kg(self):
        assert clamp_load(100, 120, "kg") == 105


class TestGuardWeekLoads:
    def test_unsafe_jump_clamped_with_note(self):
        prev = make_program(weeks=1).weeks[0]
        proposed = week_of(bench_rx(load=250))
        result = guard_week(proposed, "strength", "lb", previous=prev)
        assert result.week.sessions[0].exercises[0].load == 190
        assert any("capped" in n for n in result.notes)

    def test_moving_lift_to_another_day_still_clamped(self):
        """Reviewer M4: bypass by changing the day."""
        prev = make_program(weeks=1).weeks[0]          # bench on day 1 at 185
        proposed = week_of(bench_rx(load=400), day=3)
        result = guard_week(proposed, "strength", "lb", previous=prev)
        assert result.week.sessions[0].exercises[0].load == 190

    def test_zero_load_on_loaded_lift_restored(self):
        """Reviewer M4: bypass by zeroing the load."""
        prev = make_program(weeks=1).weeks[0]
        result = guard_week(week_of(bench_rx(load=0)), "strength", "lb", previous=prev)
        assert result.week.sessions[0].exercises[0].load == 185
        assert any("load 0" in n for n in result.notes)

    def test_zero_load_with_no_history_is_violation(self):
        result = guard_week(week_of(bench_rx(load=0)), "strength", "lb")
        assert result.violations

    def test_bodyweight_zero_allowed(self):
        pullup = PrescribedExercise(slot_id="pullup", exercise_id="pull_up", sets=3, reps=6,
                                    reps_max=10, load=0, target_rpe=8)
        result = guard_week(week_of(pullup), "hypertrophy", "lb")
        assert result.notes == [] and result.violations == []

    def test_falls_back_to_last_logged_load(self):
        result = guard_week(week_of(bench_rx(load=300)), "strength", "lb", last_loads={"bench_press": 185})
        assert result.week.sessions[0].exercises[0].load == 190

    def test_two_slots_same_lift_compared_separately(self):
        """Reviewer M5: top set and back-offs don't collide."""
        top = squat_rx(slot_id="squat_top", sets=1, reps=3, load=315)
        backoff = squat_rx(slot_id="squat_backoff", load=275)
        prev = week_of(top, backoff, n=1)
        proposed = week_of(top.model_copy(update={"load": 320}), backoff.model_copy(update={"load": 280}))
        result = guard_week(proposed, "strength", "lb", previous=prev)
        loads = {e.slot_id: e.load for e in result.week.sessions[0].exercises}
        assert loads == {"squat_top": 320, "squat_backoff": 280}
        assert result.notes == []


class TestGuardWeekRules:
    def test_reps_clamped_to_limit(self):
        result = guard_week(week_of(bench_rx(reps=15)), "strength", "lb")
        assert result.week.sessions[0].exercises[0].reps == 8      # strength main limit 1-8

    def test_timed_exercise_skips_rep_limit(self):
        plank = PrescribedExercise(slot_id="plank", exercise_id="plank", sets=3, reps=60, load=0, target_rpe=8)
        assert guard_week(week_of(plank), "hypertrophy", "lb").week.sessions[0].exercises[0].reps == 60

    def test_target_rpe_clamped(self):
        result = guard_week(week_of(bench_rx(target_rpe=10)), "strength", "lb")
        assert result.week.sessions[0].exercises[0].target_rpe == 9      # main lifts: 1-3 RIR

    def test_deload_allows_lower_rpe(self):
        result = guard_week(week_of(bench_rx(target_rpe=5), deload=True), "strength", "lb")
        assert result.week.sessions[0].exercises[0].target_rpe == 5      # 7 - 2 deload RIR

    def test_volume_cap_violation(self):
        """Reviewer M7: 30 weekly chest sets must be flagged."""
        presses = [bench_rx(slot_id=f"b{i}", sets=10) for i in range(3)]
        result = guard_week(week_of(*presses), "hypertrophy", "lb")
        assert any("chest" in v and "max" in v for v in result.violations)

    def test_volume_ramp_violation(self):
        prev = week_of(lateral_rx(sets=3), n=1)
        proposed = week_of(lateral_rx(sets=8))
        result = guard_week(proposed, "hypertrophy", "lb", previous=prev)
        assert any("side_delts" in v and "ramp" in v for v in result.violations)

    def test_weekly_muscle_sets(self):
        assert weekly_muscle_sets(week_of(bench_rx())) == {"chest": 3, "triceps": 1.5, "front_delts": 1.5}


class TestExerciseValidation:
    def test_unknown_ids_rejected(self):
        with pytest.raises(UnknownExerciseError):
            ensure_known_exercises(week_of(bench_rx(exercise_id="bench")))
