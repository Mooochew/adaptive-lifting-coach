import pytest

from app.exercises import get_exercise, muscle_credits
from app.rules import (
    HYPERTROPHY_COMPOUND,
    HYPERTROPHY_ISOLATION,
    STRENGTH_MAIN,
    rules_for_exercise,
)
from app.schemas import PrescribedExercise, Program, Session, SetLog, Week
from app.training import (
    compute_metrics,
    detect_e1rm_plateau,
    detect_performance_plateau,
    detect_regression,
    epley_e1rm,
    is_hard_set,
    load_from_recent_set,
    mark_prs,
    round_to_increment,
    set_e1rm,
    suggest_next_step,
)
from tests.conftest import bench_rx, bench_sets, lateral_rx, make_program, sets_of, warmups


def lift(metrics, exercise_id):
    return next(l for l in metrics.lifts if l.exercise_id == exercise_id)


class TestFormulas:
    def test_epley(self):
        assert epley_e1rm(225, 1) == 225
        assert epley_e1rm(185, 5) == pytest.approx(215.8, abs=0.05)
        assert epley_e1rm(0, 5) == 0 and epley_e1rm(185, 0) == 0

    def test_rpe_adjusted_e1rm(self):
        # 5 reps @ RPE 8 = 2 reps in reserve -> treated as a 7-rep max
        assert set_e1rm(185, 5, 8) == epley_e1rm(185, 7)
        assert set_e1rm(185, 5, None) == epley_e1rm(185, 5)

    def test_rpe_adjusted_e1rm_rises_as_rpe_drops(self):
        assert set_e1rm(185, 5, 8) > set_e1rm(185, 5, 9)

    def test_e1rm_skipped_when_reps_plus_rir_over_10(self):
        assert set_e1rm(135, 8, 7) is None      # 8 + 3 = 11
        assert set_e1rm(20, 15, 9) is None

    def test_rounding(self):
        assert round_to_increment(187.4, "lb") == 185
        assert round_to_increment(81.5, "kg") == 82.5

    def test_load_from_recent_set_rounds_down(self):
        # 190 x 5 -> e1RM 221.7; 5 reps @ RPE 8 (7-rep effort) -> 179.7 -> 175, not the nearer 180
        assert load_from_recent_set(190, 5, 5, 8, "lb") == 175

    def test_load_from_a_single(self):
        # A 1-rep set is the max itself: 300 -> 5 reps @ RPE 8 -> 243.2 -> 240
        assert load_from_recent_set(300, 1, 5, 8, "lb") == 240
        assert load_from_recent_set(140, 1, 5, 8, "kg") == 112.5


class TestHardSets:
    def _log(self, rpe, completed=True, set_type="working"):
        return SetLog(week=1, day=1, exercise_id="db_curl", set_number=1, weight=30,
                      reps=10, rpe=rpe, completed=completed, set_type=set_type)

    def test_thresholds(self):
        assert is_hard_set(self._log(rpe=6)) is True       # RIR 4
        assert is_hard_set(self._log(rpe=5)) is False      # RIR 5
        assert is_hard_set(self._log(rpe=None)) is True    # unrated counts
        assert is_hard_set(self._log(rpe=9, completed=False)) is False

    def test_warmups_never_count(self):
        assert is_hard_set(self._log(rpe=None, set_type="warmup")) is False

    def test_backoffs_count(self):
        assert is_hard_set(self._log(rpe=7, set_type="backoff")) is True


class TestMuscleCredits:
    def test_primary_1_secondary_half(self):
        assert muscle_credits("bench_press") == {"chest": 1.0, "triceps": 0.5, "front_delts": 0.5}


class TestRuleSelection:
    @pytest.mark.parametrize("goal,exercise,expected", [
        ("strength", "bench_press", STRENGTH_MAIN),
        ("strength", "leg_press", HYPERTROPHY_COMPOUND),       # accessories use double progression
        ("strength", "lateral_raise", HYPERTROPHY_ISOLATION),
        ("hypertrophy", "bench_press", HYPERTROPHY_COMPOUND),
        ("general", "bench_press", STRENGTH_MAIN),
        ("general", "lateral_raise", HYPERTROPHY_ISOLATION),
    ])
    def test_rules(self, goal, exercise, expected):
        assert rules_for_exercise(goal, get_exercise(exercise)) == expected


class TestPlateauHelpers:
    def test_e1rm_plateau(self):
        assert detect_e1rm_plateau([200, 200, 200]) is False
        assert detect_e1rm_plateau([210, 215, 215, 214, 215]) is True
        assert detect_e1rm_plateau([200, 205, 210, 215]) is False

    def test_prs(self):
        assert mark_prs([(185, 5), (185, 6), (185, 6), (190, 5), (185, 5)]) == [True, True, False, True, False]

    def test_performance_plateau(self):
        assert detect_performance_plateau([True, True, False, False, False]) is True
        assert detect_performance_plateau([True, False, False, True]) is False

    def test_regression_needs_two_real_drops(self):
        # (load, avg reps, avg rpe)
        assert detect_regression([(185, 5, 8), (185, 4, 8), (185, 3, 8)]) is True
        assert detect_regression([(185, 5, 8), (185, 4, 8), (185, 4.5, 8)]) is False
        assert detect_regression([(185, 5, 8), (185, 5, 9), (185, 5, 10)]) is True    # same reps, much harder

    def test_planned_jump_is_not_a_regression(self):
        """Reviewer minor: a reset to the bottom of the range after a weight jump isn't a drop."""
        assert detect_regression([(185, 10, 8), (190, 6, 8), (190, 5.67, 8)]) is False


class TestNextStepStrength:
    """Main lifts: 3x3-5 @ RPE 8 at 185. Reps first, weight only at the top of the range."""
    info = get_exercise("bench_press")
    rx = bench_rx(reps=3, reps_max=5)

    def step(self, sets, rx=None, prev=None):
        rx = rx or self.rx
        return suggest_next_step(STRENGTH_MAIN, self.info, rx, sets, "lb", prev, rx if prev else None)

    def test_good_session_inside_range_adds_reps_not_weight(self):
        step, _, load, reps = self.step(bench_sets(1, 185, 4, 8))
        assert step == "add_reps" and load == 185 and reps == 5

    def test_add_reps_targets_weakest_set_plus_one(self):
        step, _, _, reps = self.step(bench_sets(1, 185, [5, 4, 3], 8))
        assert step == "add_reps" and reps == 4

    def test_add_weight_only_at_top_of_range(self):
        step, _, load, reps = self.step(bench_sets(1, 185, 5, 8))
        assert step == "add_load" and load == 190 and reps == 3     # +2.5%, reset to bottom

    def test_main_lift_step_rounds_down(self):
        rx = bench_rx(reps=3, reps_max=5, load=315)
        assert self.step(bench_sets(1, 315, 5, 8), rx)[2] == 320     # 315 * 1.025 = 322.9 -> DOWN to 320

    def test_main_lift_step_is_at_least_one_min_step(self):
        assert self.step(bench_sets(1, 185, 5, 8))[2] == 190          # 189.6 -> 185, but min +5

    def test_default_window_when_no_top_given(self):
        # 3x5 with no reps_max -> range 5-7, so 5 reps is not yet a reason to add weight
        step, _, _, reps = self.step(bench_sets(1, 185, 5, 8), bench_rx())
        assert step == "add_reps" and reps == 6

    def test_rpe_drift_within_tolerance_still_progresses(self):
        sets = bench_sets(1, 185, 5, 8)
        sets[2].rpe = 8.5          # avg 8.17 <= 8 + 0.5
        assert self.step(sets)[0] == "add_load"

    def test_top_reps_too_hard_holds(self):
        assert self.step(bench_sets(1, 185, 5, 9))[0] == "hold"

    def test_inside_range_too_hard_holds_reps(self):
        step, _, load, reps = self.step(bench_sets(1, 185, 4, 9))
        assert step == "hold" and load == 185 and reps == 4

    def test_hold_when_below_bottom(self):
        assert self.step(bench_sets(1, 185, 2, 10))[0] == "hold"

    def test_reduce_when_short_twice_and_stuck(self):
        step, _, load, _ = self.step(bench_sets(2, 185, 2, 10), prev=bench_sets(1, 185, 2, 10))
        assert step == "reduce" and load == 170       # 185 * 0.925 = 171.1 -> 170

    def test_no_reduce_while_reps_improve(self):
        """Reviewer M2: short twice but improving -> keep going, don't cut the weight."""
        step, _, load, reps = self.step(bench_sets(2, 185, [3, 2, 2], 9), prev=bench_sets(1, 185, 2, 9))
        assert step == "add_reps" and load == 185 and reps == 3

    def test_below_range_right_after_a_jump_is_patience_not_failure(self):
        step, reason, load, _ = self.step(bench_sets(2, 190, [3, 2, 2], 9), prev=bench_sets(1, 185, 5, 8))
        assert step == "add_reps" and load == 190 and "improving" in reason

    def test_lighter_than_prescribed_is_not_success(self):
        step, _, load, _ = self.step(bench_sets(1, 135, 5, 6))
        assert step == "hold" and load == 185

    def test_partial_session_holds(self):
        assert self.step(bench_sets(1, 185, 5, 8, sets=2))[0] == "hold"


class TestNextStepDoubleProgression:
    """Lateral raise 3x12-15 @ RPE 9 at 20 lb. The next step (+5 lb) is +25%, so the overshoot rule applies."""
    info = get_exercise("lateral_raise")

    def step(self, reps, rpe, rx=None, load=20):
        sets = sets_of("lateral_raise", 1, load, reps, rpe)
        return suggest_next_step(HYPERTROPHY_ISOLATION, self.info, rx or lateral_rx(load=load), sets, "lb")

    def test_add_reps_inside_range(self):
        step, _, load, reps = self.step(13, 9)
        assert step == "add_reps" and load == 20 and reps == 14

    def test_large_jump_requires_overshoot(self):
        """Reviewer M2: 20 -> 25 lb is +25%; build to top + 3 (18) before jumping."""
        step, reason, load, reps = self.step(15, 9)
        assert step == "add_reps" and load == 20 and reps == 16 and "25% jump" in reason

    def test_jump_after_overshoot(self):
        step, _, load, reps = self.step(18, 9)
        assert step == "add_load" and load == 25 and reps == 12

    def test_small_relative_step_jumps_at_top(self):
        rx = PrescribedExercise(slot_id="curl", exercise_id="leg_curl", sets=3, reps=10, reps_max=15,
                                load=90, target_rpe=9)
        sets = sets_of("leg_curl", 1, 90, 15, 9)
        step, _, load, _ = suggest_next_step(HYPERTROPHY_ISOLATION, get_exercise("leg_curl"), rx, sets, "lb")
        assert step == "add_load" and load == 95           # +5.6%: no overshoot needed

    def test_top_reps_but_too_hard_holds(self):
        # target 9 + 0.5 tolerance; RPE 10 on every set is a grind
        assert self.step(18, 10)[0] == "hold"

    def test_below_bottom_holds(self):
        assert self.step(10, 10)[0] == "hold"

    def test_jump_then_improving_below_range_never_bounces_back(self):
        """Reviewer M2 scenario: 25 x 8,8,7 then 25 x 9,9,8 must NOT reduce back to 20."""
        rx = lateral_rx(load=25)
        prev = sets_of("lateral_raise", 1, 25, [8, 8, 7], 9)
        cur = sets_of("lateral_raise", 2, 25, [9, 9, 8], 9)
        step, _, load, reps = suggest_next_step(HYPERTROPHY_ISOLATION, self.info, rx, cur, "lb", prev, rx)
        assert step == "add_reps" and load == 25 and reps == 9

    def test_bodyweight_progression(self):
        rx = PrescribedExercise(slot_id="pullup", exercise_id="pull_up", sets=3, reps=6, reps_max=10,
                                load=0, target_rpe=8)
        sets = sets_of("pull_up", 1, 0, 10, 8)
        step, reason, load, _ = suggest_next_step(HYPERTROPHY_COMPOUND, get_exercise("pull_up"), rx, sets, "lb")
        assert step == "add_load" and load == 0 and "harder variation" in reason

    def test_weighted_bodyweight_lift_progresses_the_added_weight(self):
        """Reviewer minor: a pull-up done with +25 lb should suggest +30, not 0.
        (25 -> 30 is +20%, so the overshoot rule wants 10 + 3 = 13 reps first.)"""
        rx = PrescribedExercise(slot_id="pullup", exercise_id="pull_up", sets=3, reps=6, reps_max=10,
                                load=0, target_rpe=8)
        sets = sets_of("pull_up", 1, 25, 13, 8)
        step, _, load, _ = suggest_next_step(HYPERTROPHY_COMPOUND, get_exercise("pull_up"), rx, sets, "lb")
        assert step == "add_load" and load == 30

    def test_unknown_load_with_failed_set_asks_for_a_weight(self):
        """Reviewer m2: never suggest 0 lb for a loaded lift with no known weight."""
        rx = PrescribedExercise(slot_id="row", exercise_id="barbell_row", sets=3, reps=6, reps_max=10,
                                load=None, target_rpe=8)
        failed = [SetLog(week=1, day=1, slot_id="row", exercise_id="barbell_row", set_number=1,
                         weight=0, reps=0, completed=False)]
        step, reason, load, _ = suggest_next_step(HYPERTROPHY_COMPOUND, get_exercise("barbell_row"), rx, failed, "lb")
        assert step == "hold" and load is None and "Pick a working weight" in reason

    def test_timed_hold_at_top_moves_to_harder_variation(self):
        rx = PrescribedExercise(slot_id="core", exercise_id="plank", sets=3, reps=30, reps_max=60,
                                load=0, target_rpe=8)
        step, reason, _, _ = suggest_next_step(HYPERTROPHY_ISOLATION, get_exercise("plank"), rx,
                                               sets_of("plank", 1, 0, 60, 8), "lb")
        assert step == "add_load" and "harder variation" in reason

    def test_range_falls_back_to_default_window(self):
        rx = lateral_rx(reps=10, reps_max=None)         # isolation window +5 -> 10-15, overshoot -> 18
        assert self.step(15, 9, rx)[0] == "add_reps"
        assert self.step(18, 9, rx)[0] == "add_load"


class TestComputeMetrics:
    def test_empty_logs(self, program):
        m = compute_metrics("hypertrophy", program, [])
        assert m.lifts == [] and m.weekly_volume == [] and m.deload.recommended is False

    def test_warmups_ignored(self, program):
        """Reviewer M1: warm-ups must not cause 'reduce' or inflate volume."""
        logs = warmups(1) + bench_sets(1, 185, 5, 8) + warmups(2) + bench_sets(2, 190, 5, 8)
        m = compute_metrics("strength", program, logs)
        bench = lift(m, "bench_press")
        assert bench.slots[0].next_step == "add_reps"     # not "reduce" from the 135x3 warm-up
        assert bench.missed_reps == 0
        chest = [v for v in m.muscle_volume if v.week == 1 and v.muscle == "chest"][0]
        assert chest.hard_sets == 3.0

    def test_double_progression_across_sets_is_not_a_plateau(self, program):
        """Reviewer M2: 12/10/9 -> 12/11/10 -> 12/12/11 -> 12/12/12 is progress."""
        logs = []
        for w, reps in enumerate([[12, 10, 9], [12, 11, 10], [12, 12, 11], [12, 12, 12]], start=1):
            logs += sets_of("lateral_raise", w, 20, reps, 9)
        lat = lift(compute_metrics("hypertrophy", program, logs), "lateral_raise")
        assert [e.pr for e in lat.slots[0].exposures] == [True, True, True, True]
        assert lat.plateau is False

    def test_no_reps_gained_is_a_plateau(self, program):
        logs = sum((sets_of("lateral_raise", w, 20, 12, 9) for w in range(1, 5)), [])
        assert lift(compute_metrics("hypertrophy", program, logs), "lateral_raise").plateau is True

    def test_lower_rpe_at_same_load_is_not_a_strength_plateau(self, program):
        """Reviewer M3: 185x5 at RPE 9 -> 8 is getting stronger."""
        logs = [s for w, rpe in enumerate([9, 9, 8.5, 8], start=1) for s in bench_sets(w, 185, 5, rpe)]
        assert lift(compute_metrics("strength", program, logs), "bench_press").plateau is False

    def test_flat_strength_is_a_plateau(self, program):
        logs = sum((bench_sets(w, 185, 5, 8) for w in range(1, 5)), [])
        bench = lift(compute_metrics("strength", program, logs), "bench_press")
        assert bench.plateau is True and bench.plateau_suggestion

    def test_lighter_than_prescribed(self, program):
        """Reviewer M8."""
        bench = lift(compute_metrics("strength", program, bench_sets(1, 135, 5, 6)), "bench_press")
        assert bench.slots[0].next_step == "hold"

    def test_same_lift_two_slots_same_day(self):
        """Reviewer M5: top set and back-offs are tracked separately."""
        week = Week(week=1, sessions=[Session(day=1, focus="Lower", exercises=[
            PrescribedExercise(slot_id="squat_top", exercise_id="back_squat", sets=1, reps=3, reps_max=3,
                               load=315, target_rpe=8),
            PrescribedExercise(slot_id="squat_backoff", exercise_id="back_squat", sets=3, reps=4, reps_max=5,
                               load=275, target_rpe=7.5),
        ])])
        program = Program(weeks=[week])
        logs = (sets_of("back_squat", 1, 315, 3, 8, sets=1, slot_id="squat_top")
                + sets_of("back_squat", 1, 275, 5, 7.5, slot_id="squat_backoff"))
        squat = lift(compute_metrics("strength", program, logs), "back_squat")
        slots = {s.slot_id: s for s in squat.slots}
        assert slots["squat_top"].suggested_load == 320       # 315 * 1.025 = 322.9 -> down to 320
        assert slots["squat_backoff"].suggested_load == 280   # 275 * 1.025 = 281.9 -> down to 280

    def test_session_in_progress_shows_the_target_for_it(self, program):
        """Reviewer m1: mid-session, show what to aim for (from last week), not 'only 2 of 3 logged'."""
        # fixture bench: 3x5 (default window -> 5-7); last week 5,5,5 -> this session aim for 6
        logs = bench_sets(1, 185, 5, 8) + bench_sets(2, 185, 6, 8, sets=2)
        slot = lift(compute_metrics("strength", program, logs), "bench_press").slots[0]
        assert slot.in_progress is True
        assert slot.next_step == "add_reps" and slot.suggested_reps == 6
        assert slot.next_step_reason.startswith("This session:")

    def test_first_session_in_progress(self, program):
        slot = lift(compute_metrics("strength", program, bench_sets(1, 185, 5, 8, sets=1)), "bench_press").slots[0]
        assert slot.in_progress and slot.next_step == "hold" and slot.next_step_reason.startswith("First session")

    def test_finished_session_is_not_in_progress(self, program):
        slot = lift(compute_metrics("strength", program, bench_sets(1, 185, 4, 8)), "bench_press").slots[0]
        assert slot.in_progress is False

    def test_unrated_sets_use_target_rpe_for_e1rm(self, program):
        """Reviewer minor: unrated sets aren't treated as all-out (RPE 10)."""
        m = compute_metrics("strength", program, bench_sets(1, 185, 5, None))
        # 3x5 target RPE 8 -> 5 + 2 RIR -> Epley(185, 7)
        assert lift(m, "bench_press").best_e1rm == epley_e1rm(185, 7)

    def test_volume_contributors(self, program):
        logs = bench_sets(1, 185, 5, 8) + sets_of("triceps_pushdown", 1, 50, 12, 9, sets=2)
        m = compute_metrics("hypertrophy", program, logs)
        triceps = next(v for v in m.muscle_volume if v.muscle == "triceps")
        assert triceps.hard_sets == 3.5
        assert [(c.exercise_id, c.hard_sets) for c in triceps.contributors] == [
            ("triceps_pushdown", 2.0), ("bench_press", 1.5)]

    def test_untargeted_muscles_have_no_target(self, program):
        m = compute_metrics("hypertrophy", program, sets_of("back_squat", 1, 225, 5, 8, day=2))
        lower_back = next(v for v in m.muscle_volume if v.muscle == "lower_back")
        quads = next(v for v in m.muscle_volume if v.muscle == "quads")
        assert lower_back.status == "no_target" and lower_back.target_low is None
        assert quads.status == "below" and quads.target_low == 10

    def test_heavy_and_light_days_are_not_a_regression(self):
        """Reviewer m3: different slots on different days aren't compared."""
        def week(n):
            return Week(week=n, sessions=[
                Session(day=d, focus="Lower", exercises=[
                    PrescribedExercise(slot_id="squat", exercise_id="back_squat", sets=3, reps=5, load=load, target_rpe=8)
                ]) for d, load in [(1, 300), (3, 270), (5, 240)]
            ])
        program = Program(weeks=[week(1)])
        logs = []
        for d, load in [(1, 300), (3, 270), (5, 240)]:
            logs += sets_of("back_squat", 1, load, 5, 8, day=d)
        squat = lift(compute_metrics("strength", program, logs), "back_squat")
        assert squat.regressing is False
        assert all(s.next_step == "add_reps" for s in squat.slots)   # each judged on its own slot

    def test_deload_week_does_not_trigger_another_deload(self):
        """Reviewer M6."""
        program = make_program(weeks=5, deload_weeks=(5,))
        logs = []
        for w in range(1, 5):
            logs += bench_sets(w, 175 + 5 * w, 5, 8)
            logs += sets_of("back_squat", w, 215 + 10 * w, 5, 8, day=2)
        logs += bench_sets(5, 150, 5, 5) + sets_of("back_squat", 5, 185, 5, 5, day=2)
        m = compute_metrics("strength", program, logs)
        assert m.deload.recommended is False
        assert all(not l.regressing for l in m.lifts)

    def test_scheduled_deload(self, program):
        logs = sum((bench_sets(w, 180 + 5 * w, 5, 8) for w in range(1, 5)), [])
        d = compute_metrics("strength", program, logs).deload
        assert d.scheduled is True and d.next_week == 5

    def test_reactive_deload_on_effort(self, program):
        logs = bench_sets(1, 185, 5, 9.5) + bench_sets(2, 185, 5, 9.5)
        d = compute_metrics("strength", program, logs).deload
        assert d.recommended and not d.scheduled and any("Effort" in r for r in d.reasons)

    def test_isolation_plateaus_alone_do_not_trigger_deload(self, program):
        """Reviewer m4."""
        logs = []
        for w in range(1, 5):
            logs += sets_of("lateral_raise", w, 20, 12, 9)
            logs += sets_of("db_curl", w, 30, 12, 9, slot_id=None)
        m = compute_metrics("hypertrophy", program, logs)
        assert not any("Plateau" in r for r in m.deload.reasons)

    def test_muscle_volume_status(self, program):
        m = compute_metrics("hypertrophy", program, bench_sets(1, 185, 5, 8))
        chest = [v for v in m.muscle_volume if v.muscle == "chest"][0]
        assert chest.hard_sets == 3 and chest.status == "below" and chest.target_low == 10

    def test_unrated_sets_flagged(self, program):
        m = compute_metrics("hypertrophy", program, bench_sets(1, 185, 5, None))
        assert lift(m, "bench_press").unrated_sets == 3
        assert m.weekly_volume[0].unrated_sets == 3 and m.weekly_volume[0].hard_sets == 3

    def test_other_blocks_ignored(self, program):
        logs = bench_sets(1, 185, 5, 8)
        for log in logs:
            log.block = 2
        assert compute_metrics("strength", program, logs).lifts == []

    def test_extra_sets_not_counted_as_missed(self, program):
        logs = bench_sets(1, 185, 5, 8) + sets_of("bench_press", 1, 185, [2], 10, sets=1)
        logs[-1].set_number = 4
        assert lift(compute_metrics("strength", program, logs), "bench_press").missed_reps == 0

    def test_skipped_sessions(self, program):
        logs = bench_sets(1, 185, 5, 8) + bench_sets(2, 190, 5, 8)
        assert compute_metrics("strength", program, logs).sessions_skipped == ["W1D2"]

    def test_unprescribed_lift_still_tracked(self, program):
        m = compute_metrics("hypertrophy", program, sets_of("db_curl", 1, 30, 10, None, sets=1))
        curl = lift(m, "db_curl")
        assert curl.slots == [] and curl.unrated_sets == 1
