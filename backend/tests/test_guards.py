import pytest
from pydantic import ValidationError

from app.guards import (
    UnknownExerciseError,
    clamp_load,
    ensure_known_exercises,
    guard_program,
    guard_week,
    weekly_muscle_sets,
)
from app.schemas import LiftEstimate, PrescribedExercise, Session, SlotMetrics, Week
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

    def test_zero_load_with_no_history_becomes_unknown(self):
        result = guard_week(week_of(bench_rx(load=0)), "strength", "lb")
        assert result.week.sessions[0].exercises[0].load is None
        assert result.violations == []

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


class TestNoHistoryLoads:
    """A lift with no previous or logged load: the AI can't invent a weight."""
    BENCH_185x5 = [LiftEstimate(exercise_id="bench_press", weight=185, reps=5)]

    def load_after(self, proposed, **kw):
        result = guard_week(week_of(bench_rx(load=proposed)), "strength", "lb", **kw)
        return result.week.sessions[0].exercises[0].load, result

    def test_no_estimate_clears_the_load(self):
        """Reviewer finding: 900 lb used to pass for a lift with no history."""
        load, result = self.load_after(900)
        assert load is None
        assert any("no history" in n for n in result.notes)
        assert result.violations == []

    def test_estimate_caps_the_load(self):
        # 185 x 5 -> e1RM 215.8; 5 reps @ RPE 8 (7-rep effort) -> 175
        load, result = self.load_after(900, estimates=self.BENCH_185x5)
        assert load == 175
        assert any("recent set" in n for n in result.notes)

    def test_estimate_allows_one_step_of_leeway(self):
        assert self.load_after(180, estimates=self.BENCH_185x5)[0] == 180
        assert self.load_after(185, estimates=self.BENCH_185x5)[0] == 175

    def test_load_matching_the_estimate_has_no_note(self):
        load, result = self.load_after(175, estimates=self.BENCH_185x5)
        assert load == 175 and result.notes == []

    def test_estimate_for_another_lift_does_not_count(self):
        squat = [LiftEstimate(exercise_id="back_squat", weight=315, reps=5)]
        assert self.load_after(185, estimates=squat)[0] is None

    def test_bodyweight_extra_load_cleared(self):
        pullup = PrescribedExercise(slot_id="pullup", exercise_id="pull_up", sets=3, reps=6,
                                    reps_max=10, load=90, target_rpe=8)
        result = guard_week(week_of(pullup), "hypertrophy", "lb")
        assert result.week.sessions[0].exercises[0].load == 0

    def test_history_still_wins_over_the_estimate(self):
        load, _ = self.load_after(300, estimates=self.BENCH_185x5, last_loads={"bench_press": 200})
        assert load == 210                                     # +5% of 200, not the estimate

    def test_zero_load_with_an_estimate_gets_the_supported_load(self):
        load, result = self.load_after(0, estimates=self.BENCH_185x5)
        assert load == 175
        assert any("from your recent set" in n for n in result.notes)
        assert not any("capped" in n for n in result.notes)   # it went up, so it isn't "capped"

    def test_tiny_load_is_never_rounded_to_zero(self):
        assert self.load_after(2, estimates=self.BENCH_185x5)[0] == 5

    def test_estimate_too_light_to_support_a_load_leaves_it_unknown(self):
        tiny = [LiftEstimate(exercise_id="bench_press", weight=5, reps=1)]
        assert self.load_after(10, estimates=tiny)[0] is None

    def test_odd_load_is_rounded(self):
        load, result = self.load_after(177, estimates=self.BENCH_185x5)
        assert load == 175 and any("rounded" in n for n in result.notes)

    def test_kg_leeway_is_one_kg_step(self):
        # 100 kg x 5 -> 5 reps @ RPE 8 supports 94.6 -> 92.5 kg; leeway 2.5 kg
        estimate = [LiftEstimate(exercise_id="bench_press", weight=100, reps=5)]
        loads = {p: guard_week(week_of(bench_rx(load=p)), "strength", "kg", estimates=estimate)
                 .week.sessions[0].exercises[0].load for p in (95, 96)}
        assert loads == {95: 95, 96: 92.5}


class TestBorrowedBaseline:
    """A slot with no load of its own may borrow a ceiling from the same lift elsewhere,
    but never a floor: the guard must not push a light slot up to a heavy one."""

    def prev_week(self):
        heavy = bench_rx(slot_id="bench_heavy", load=205)
        volume = bench_rx(slot_id="bench_volume", reps=8, load=None)
        return Week(week=1, sessions=[Session(day=1, focus="A", exercises=[heavy]),
                                      Session(day=3, focus="B", exercises=[volume])])

    def proposed(self, volume_load):
        return Week(week=2, sessions=[
            Session(day=1, focus="A", exercises=[bench_rx(slot_id="bench_heavy", load=205)]),
            Session(day=3, focus="B", exercises=[bench_rx(slot_id="bench_volume", reps=8, load=volume_load)]),
        ])

    def volume_load(self, proposed_load, **kw):
        result = guard_week(self.proposed(proposed_load), "strength", "lb", previous=self.prev_week(), **kw)
        return result.week.sessions[1].exercises[0].load

    def test_light_slot_is_not_raised_to_the_heavy_slot(self):
        """Reviewer finding: 155 used to be raised to 165 ('capped vs 205')."""
        assert self.volume_load(155) == 155

    def test_borrowed_baseline_still_caps_upward(self):
        assert self.volume_load(400) == 215                   # 205 + 5%

    def test_unknown_load_is_not_filled_from_another_slot(self):
        assert self.volume_load(None) is None

    def test_last_logged_load_only_caps_upward_too(self):
        result = guard_week(week_of(bench_rx(load=95)), "strength", "lb", last_loads={"bench_press": 185})
        assert result.week.sessions[0].exercises[0].load == 95

    def test_borrowed_load_is_rounded(self):
        assert self.volume_load(157) == 155

    def test_what_the_lifter_logged_beats_a_lighter_day(self):
        """Reviewer finding: pushdowns lifted at 50 were cut to 25 because Day 5's plan said 20."""
        light_day = PrescribedExercise(slot_id="pushdown", exercise_id="triceps_pushdown", sets=3, reps=10,
                                       reps_max=15, load=20, target_rpe=9)
        prev = Week(week=1, sessions=[
            Session(day=1, focus="A", exercises=[light_day.model_copy(update={"load": None})]),
            Session(day=5, focus="B", exercises=[light_day])])
        proposed = Week(week=2, sessions=[
            Session(day=1, focus="A", exercises=[light_day.model_copy(update={"load": 50})]),
            Session(day=5, focus="B", exercises=[light_day])])
        result = guard_week(proposed, "hypertrophy", "lb", previous=prev, last_loads={"triceps_pushdown": 50})
        assert result.week.sessions[0].exercises[0].load == 50

    def test_bodyweight_slot_at_zero_stays_zero_next_to_a_weighted_day(self):
        weighted = PrescribedExercise(slot_id="dip", exercise_id="dip", sets=3, reps=6, reps_max=10,
                                      load=45, target_rpe=8)
        prev = Week(week=1, sessions=[Session(day=2, focus="A", exercises=[weighted])])
        proposed = Week(week=2, sessions=[Session(day=3, focus="B", exercises=[weighted.model_copy(
            update={"load": 0})])])
        assert guard_week(proposed, "hypertrophy", "lb", previous=prev).week.sessions[0].exercises[0].load == 0

    def test_bodyweight_slot_keeps_small_added_weight(self):
        """Reviewer finding: +10 lb on a bodyweight dip day was forced to +40 lb."""
        weighted = PrescribedExercise(slot_id="dip", exercise_id="dip", sets=3, reps=6, reps_max=10,
                                      load=45, target_rpe=8)
        bodyweight = weighted.model_copy(update={"load": 0})
        prev = Week(week=1, sessions=[Session(day=2, focus="A", exercises=[weighted]),
                                      Session(day=3, focus="B", exercises=[bodyweight])])
        proposed = Week(week=2, sessions=[Session(day=2, focus="A", exercises=[weighted]),
                                          Session(day=3, focus="B", exercises=[bodyweight.model_copy(
                                              update={"load": 10})])])
        result = guard_week(proposed, "hypertrophy", "lb", previous=prev)
        assert result.week.sessions[1].exercises[0].load == 10


class TestDoubleProgressionWithoutOwnSlot:
    """Security finding: renaming a slot or moving a lift to another day skipped the
    'weight only goes up once earned' rule (squat 200 -> 210 although the engine said hold)."""

    def decisions(self, step, load, slot="squat", day=1):
        return {(day, slot): SlotMetrics(slot_id=slot, day=day, exposures=[], plateau=False, regressing=False,
                                         in_progress=False, next_step=step, next_step_reason="",
                                         suggested_load=load, suggested_reps=5)}

    def squat_load(self, slot, day, slot_metrics):
        prev = week_of(squat_rx(load=200), n=1)
        proposed = Week(week=2, sessions=[Session(day=day, focus="x",
                                                  exercises=[squat_rx(slot_id=slot, load=210)])])
        result = guard_week(proposed, "strength", "lb", previous=prev, slot_metrics=slot_metrics)
        return result.week.sessions[0].exercises[0].load

    def test_renamed_slot_cannot_add_unearned_weight(self):
        assert self.squat_load("squat2", 1, self.decisions("hold", 200)) == 200

    def test_moved_day_cannot_add_unearned_weight(self):
        assert self.squat_load("squat", 2, self.decisions("hold", 200)) == 200

    def test_earned_increase_is_allowed_up_to_the_suggestion(self):
        assert self.squat_load("squat", 2, self.decisions("add_load", 205, day=2)) == 205

    def test_deload_weeks_keep_only_the_load_caps(self):
        """Unchanged behaviour: a deload never becomes the next week's baseline, so it only gets
        the +5% cap (guard_program also checks deloads are spaced and cut volume)."""
        prev = week_of(squat_rx(load=200), n=1)
        result = guard_week(week_of(squat_rx(load=210), deload=True), "strength", "lb", previous=prev,
                            slot_metrics=self.decisions("hold", 200))
        assert result.week.sessions[0].exercises[0].load == 210


class TestApprovedLoads:
    def test_already_approved_load_for_a_new_lift_is_kept(self):
        """A lift new this week whose load was approved when the program was made keeps it."""
        rdl = PrescribedExercise(slot_id="rdl", exercise_id="romanian_deadlift", sets=3, reps=8,
                                 reps_max=12, load=185, target_rpe=8)
        week = week_of(rdl)
        result = guard_week(week, "hypertrophy", "lb", approved_loads={(1, "rdl"): 185})
        assert result.week.sessions[0].exercises[0].load == 185 and result.notes == []

    def test_a_changed_load_is_not_approved(self):
        rdl = PrescribedExercise(slot_id="rdl", exercise_id="romanian_deadlift", sets=3, reps=8,
                                 reps_max=12, load=400, target_rpe=8)
        result = guard_week(week_of(rdl), "hypertrophy", "lb", approved_loads={(1, "rdl"): 185})
        assert result.week.sessions[0].exercises[0].load is None

    def test_approval_never_skips_the_lifts_own_history(self):
        """Reviewer finding (all three): a renamed slot or a new slot for a known lift kept any
        planned load. 400 lb bench with 185 history must still be capped."""
        prev = week_of(bench_rx(load=185), n=1)
        renamed = week_of(bench_rx(slot_id="bench_new", load=400))
        result = guard_week(renamed, "strength", "lb", previous=prev, approved_loads={(1, "bench_new"): 400})
        assert result.week.sessions[0].exercises[0].load == 190

    def test_approval_never_skips_the_earned_increase_rule(self):
        decisions = {(1, "squat"): SlotMetrics(slot_id="squat", day=1, exposures=[], plateau=False,
                                               regressing=False, in_progress=False, next_step="hold",
                                               next_step_reason="", suggested_load=200, suggested_reps=5)}
        result = guard_week(week_of(squat_rx(slot_id="squat2", load=210)), "strength", "lb",
                            last_loads={"back_squat": 200}, slot_metrics=decisions,
                            approved_loads={(1, "squat2"): 210})
        assert result.week.sessions[0].exercises[0].load == 200


class TestSlotEdgeCases:
    def test_swapped_exercise_in_the_same_slot_does_not_inherit_the_old_load(self):
        """The AI swaps bench for DB bench but keeps slot_id 'bench': 185 (barbell) is not its baseline."""
        prev = week_of(bench_rx(load=185), n=1)
        swapped = week_of(bench_rx(exercise_id="db_bench_press", load=185))
        result = guard_week(swapped, "hypertrophy", "lb", previous=prev)
        assert result.week.sessions[0].exercises[0].load is None
        assert any("no history" in n for n in result.notes)

    def test_loaded_lift_at_zero_with_history_elsewhere_is_left_for_the_lifter(self):
        result = guard_week(week_of(bench_rx(slot_id="bench2", load=0)), "strength", "lb",
                            last_loads={"bench_press": 185})
        assert result.week.sessions[0].exercises[0].load is None
        assert any("load 0 is not allowed" in n for n in result.notes)

    def test_logged_bodyweight_lift_may_add_one_step(self):
        """Pull-ups done at bodyweight last week: the AI may add one load step, not more,
        and the note doesn't wrongly say there's no history."""
        pullup = PrescribedExercise(slot_id="pullup", exercise_id="pull_up", sets=3, reps=6, reps_max=10,
                                    load=0, target_rpe=8)
        prev = week_of(pullup, n=1)
        loads = {}
        for proposed in (5, 25):
            result = guard_week(week_of(pullup.model_copy(update={"load": proposed})), "hypertrophy", "lb",
                                previous=prev)
            loads[proposed] = result.week.sessions[0].exercises[0].load
            assert not any("no history" in n for n in result.notes)
        assert loads == {5: 5, 25: 5}

    def test_weekly_max_is_allowed_exactly(self):
        presses = [bench_rx(slot_id="b1", sets=10), bench_rx(slot_id="b2", sets=10)]
        week = Week(week=2, sessions=[Session(day=1, focus="x", exercises=presses[:1]),
                                      Session(day=2, focus="y", exercises=presses[1:])])
        result = guard_week(week, "hypertrophy", "lb")
        assert not any("weekly sets exceeds" in v for v in result.violations)          # 20 = the max
        week.sessions[1].exercises[0].sets = 10
        week.sessions[1].exercises.append(bench_rx(slot_id="b3", exercise_id="dip", sets=1, load=0))
        assert any("chest: 21" in v for v in guard_week(week, "hypertrophy", "lb").violations)

    def test_estimate_supporting_exactly_one_step_is_used(self):
        # 7 lb x 3 -> e1RM 7.7; 5 reps @ RPE 8 supports 6.2 -> 5 lb, exactly one load step
        result = guard_week(week_of(bench_rx(load=900)), "strength", "lb",
                            estimates=[LiftEstimate(exercise_id="bench_press", weight=7, reps=3)])
        assert result.week.sessions[0].exercises[0].load == 5


class TestGuardProgram:
    def test_checks_every_week_against_the_one_before(self):
        program = make_program(weeks=2)
        program.weeks[1].sessions[0].exercises[0].load = 250        # week 2 bench: 185 -> 250
        result = guard_program(program, "strength", estimates=[
            LiftEstimate(exercise_id="bench_press", weight=215, reps=5),
            LiftEstimate(exercise_id="lateral_raise", weight=25, reps=12),
            LiftEstimate(exercise_id="back_squat", weight=265, reps=5)])
        bench = [w.sessions[0].exercises[0].load for w in result.program.weeks]
        assert bench == [185, 190]
        assert any(n.startswith("W2:") and "capped" in n for n in result.notes)

    def test_collects_violations_with_the_week(self):
        program = make_program(weeks=1)
        program.weeks[0].sessions[0].exercises[0].sets = 10
        program.weeks[0].sessions[0].exercises.append(bench_rx(slot_id="bench2", sets=10))
        result = guard_program(program, "hypertrophy")
        assert any(v.startswith("W1:") and "chest" in v for v in result.violations)

    def test_deload_week_is_not_the_baseline_for_the_next(self):
        program = make_program(weeks=3, deload_weeks=(2,))
        program.weeks[1].sessions[0].exercises[0].load = 100        # deload bench 185 -> 100
        result = guard_program(program, "strength", last_loads={"bench_press": 185, "lateral_raise": 20,
                                                                "back_squat": 225})
        assert result.program.weeks[2].sessions[0].exercises[0].load == 185

    def test_deload_needs_hard_weeks_before_it(self):
        """The AI can't call week 2 a deload to dodge the volume rules."""
        result = guard_program(make_program(weeks=3, deload_weeks=(2,)), "strength")
        assert any(v.startswith("W2:") and "deload" in v for v in result.violations)

    @pytest.mark.parametrize("weeks, deloads, flagged", [
        (5, (5,), []),
        (4, (4,), []),                  # exactly 3 hard weeks before it
        (3, (3,), [3]),                 # only 2
        (10, (5, 10), []),              # the count restarts after each deload
        (10, (5, 8), [8]),
        (6, (3, 5), [3, 5]),
    ])
    def test_deload_spacing(self, weeks, deloads, flagged):
        result = guard_program(make_program(weeks=weeks, deload_weeks=deloads), "strength")
        assert [int(v[1:v.index(":")]) for v in result.violations if "deload needs" in v] == flagged

    def test_weeks_are_checked_in_order_even_if_listed_out_of_order(self):
        program = make_program(weeks=2)
        program.weeks[1].sessions[0].exercises[0].load = 250
        program.weeks.reverse()
        result = guard_program(program, "strength", last_loads={"bench_press": 185, "lateral_raise": 20,
                                                                "back_squat": 225})
        week2 = next(w for w in result.program.weeks if w.week == 2)
        assert week2.sessions[0].exercises[0].load == 190


class TestDeloadVolume:
    """Security finding: marking a week as a deload used to switch off every volume check."""

    def test_weekly_max_still_applies(self):
        presses = [bench_rx(slot_id=f"b{i}", sets=10) for i in range(3)]
        result = guard_week(week_of(*presses, deload=True), "hypertrophy", "lb")
        assert any("chest" in v and "max" in v for v in result.violations)

    def test_per_session_cap_still_applies(self):
        presses = [bench_rx(slot_id="b1", sets=6), bench_rx(slot_id="b2", exercise_id="incline_db_press", sets=6)]
        result = guard_week(week_of(*presses, deload=True), "hypertrophy", "lb")
        assert any("chest" in v and "one session" in v for v in result.violations)

    def test_deload_cannot_add_sets_over_the_last_hard_week(self):
        prev = week_of(bench_rx(sets=3), n=1)
        result = guard_week(week_of(bench_rx(sets=5), deload=True), "hypertrophy", "lb", previous=prev)
        assert any("chest" in v and "deload" in v for v in result.violations)

    def test_deload_may_keep_the_same_sets(self):
        prev = week_of(bench_rx(sets=3), n=1)
        result = guard_week(week_of(bench_rx(sets=3, target_rpe=6), deload=True), "hypertrophy", "lb",
                            previous=prev)
        assert not any("deload" in v for v in result.violations)

    def test_normal_deload_passes(self):
        prev = week_of(bench_rx(sets=4), n=1)
        result = guard_week(week_of(bench_rx(sets=2, target_rpe=6), deload=True), "hypertrophy", "lb",
                            previous=prev)
        assert result.violations == []


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

    def test_per_session_muscle_cap_violation(self):
        """12 chest sets in one session is over the ~10/session ceiling, even if the week is fine."""
        presses = [bench_rx(slot_id="b1", sets=6), bench_rx(slot_id="b2", exercise_id="incline_db_press", sets=6)]
        result = guard_week(week_of(*presses), "hypertrophy", "lb")
        assert any("chest" in v and "one session" in v for v in result.violations)

    def test_volume_ramp_violation(self):
        prev = week_of(lateral_rx(sets=3), n=1)
        proposed = week_of(lateral_rx(sets=8))
        result = guard_week(proposed, "hypertrophy", "lb", previous=prev)
        assert any("side_delts" in v and "ramp" in v for v in result.violations)

    def test_weekly_muscle_sets(self):
        assert weekly_muscle_sets(week_of(bench_rx())) == {"chest": 3, "triceps": 1.5, "front_delts": 1.5}


class TestExerciseValidation:
    def test_unknown_ids_rejected_by_the_schema(self):
        with pytest.raises(ValidationError, match="unknown exercise id 'bench'"):
            bench_rx(exercise_id="bench")

    def test_guards_reject_unknown_ids_that_skipped_validation(self):
        week = week_of(bench_rx())
        week.sessions[0].exercises[0] = bench_rx().model_copy(update={"exercise_id": "bench"})
        with pytest.raises(UnknownExerciseError):
            ensure_known_exercises(week)
