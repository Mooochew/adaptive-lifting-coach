from app.guards import guard_week
from app.progression import advance_program, last_ended_week, last_logged_loads
from app.schemas import PrescribedExercise, SlotMetrics
from app.training import compute_metrics
from tests.conftest import bench_rx, bench_sets, make_program, sets_of


def rx(program, week, slot):
    return next(e for s in next(w for w in program.weeks if w.week == week).sessions
                for e in s.exercises if e.slot_id == slot)


def full_week1(bench_reps=5, bench_rpe=8, weight=185):
    """Week 1 fully logged: bench + lateral on day 1, squat on day 2."""
    return (bench_sets(1, weight, bench_reps, bench_rpe)
            + sets_of("lateral_raise", 1, 20, 13, 9, slot_id="lateral")
            + sets_of("back_squat", 1, 225, 5, 8, day=2, slot_id="squat"))


class TestLastEndedWeek:
    def test_nothing_logged(self):
        assert last_ended_week(make_program(), []) == 0

    def test_partial_week_has_not_ended(self):
        assert last_ended_week(make_program(), bench_sets(1, 185, 5, 8)) == 0

    def test_fully_logged_week_has_ended(self):
        assert last_ended_week(make_program(), full_week1()) == 1

    def test_moving_on_ends_the_week_even_with_skips(self):
        logs = bench_sets(1, 185, 5, 8) + bench_sets(2, 185, 5, 8)     # skipped W1 lateral + squat
        assert last_ended_week(make_program(), logs) == 1


class TestAdvance:
    def test_suggestions_written_into_next_weeks(self):
        program = make_program(weeks=4)
        logs = full_week1() + sets_of("back_squat", 1, 225, 5, 8, day=2, slot_id="squat")
        updated, changes, notes = advance_program("strength", program, full_week1(bench_reps=7))
        assert updated.adjusted_through == 1
        # bench 3x5 (window 5-7): 7,7,7 at RPE 8 -> top reached -> 185 * 1.025 rounded down, min +5 -> 190
        assert rx(updated, 2, "bench").load == 190 and rx(updated, 4, "bench").load == 190
        assert rx(updated, 1, "bench").load == 185                       # the past isn't rewritten
        assert any("bench: 185 -> 190" in c for c in changes)
        assert notes == []

    def test_reduce_is_followed_by_the_plan(self):
        """Reviewer M3: after 'reduce to 170', lifting 170 must not be called 'below prescribed'."""
        program = make_program(weeks=4)
        w1 = bench_sets(1, 185, 2, 10) + sets_of("lateral_raise", 1, 20, 13, 9, slot_id="lateral") \
            + sets_of("back_squat", 1, 225, 5, 8, day=2, slot_id="squat")
        program, _, _ = advance_program("strength", program, w1)
        w2 = bench_sets(2, 185, 2, 10) + sets_of("lateral_raise", 2, 20, 13, 9, slot_id="lateral") \
            + sets_of("back_squat", 2, 225, 5, 8, day=2, slot_id="squat")
        program, _, _ = advance_program("strength", program, w1 + w2)
        assert rx(program, 3, "bench").load == 170                       # reduce applied to week 3

        w3 = bench_sets(3, 170, 4, 8)
        m = compute_metrics("strength", program, w1 + w2 + w3)
        bench = next(s for l in m.lifts if l.exercise_id == "bench_press" for s in l.slots)
        assert "below the prescribed" not in bench.next_step_reason

    def test_idempotent(self):
        program, logs = make_program(weeks=4), full_week1()
        once, _, _ = advance_program("strength", program, logs)
        twice, changes, _ = advance_program("strength", once, logs)
        assert changes == [] and twice == once

    def test_slots_already_logged_are_not_changed(self):
        program = make_program(weeks=4)
        # Week 2 bench already started at 185 (1 of 3 sets): the decision still comes from week 1 (7,7,7 -> 190)
        logs = full_week1(bench_reps=7) + bench_sets(2, 185, 5, 8, sets=1)
        updated, _, _ = advance_program("strength", program, logs)
        assert rx(updated, 2, "bench").load == 185                       # what was prescribed when lifted stays
        assert rx(updated, 3, "bench").load == 190

    def test_deload_week_gets_the_load_cut(self):
        program = make_program(weeks=5, deload_weeks=(5,))
        updated, _, _ = advance_program("strength", program, full_week1(bench_reps=7))
        assert rx(updated, 5, "bench").load == 170                       # 190 - 10% = 171 -> down to 170

    def test_unknown_load_stays_unknown_until_lifted(self):
        program = make_program(weeks=2)
        rx(program, 1, "bench").load = None
        rx(program, 2, "bench").load = None
        logs = full_week1()
        logs = [l for l in logs if l.exercise_id != "bench_press"] + [
            l.model_copy(update={"completed": False, "reps": 0}) for l in bench_sets(1, 0, 0, None)]
        updated, _, _ = advance_program("strength", program, logs)
        assert rx(updated, 2, "bench").load is None

    def test_lift_skipped_last_week_keeps_its_weight(self):
        """Bench lifted in week 1, skipped in week 2: week 3 still knows the weight from the logs."""
        program = make_program(weeks=3)
        rx(program, 2, "bench").load = None
        logs = full_week1() + sets_of("back_squat", 3, 225, 5, 8, day=2, slot_id="squat")
        updated, _, notes = advance_program("strength", program, logs)
        assert updated.adjusted_through == 2
        assert rx(updated, 3, "bench").load == 185
        assert not any("no history" in n for n in notes)

    def test_lift_new_mid_block_keeps_its_planned_weight(self):
        """Reviewer finding: an RDL first appearing in week 2 at 185 used to be wiped to None."""
        program = make_program(weeks=2)
        week2_day2 = program.weeks[1].sessions[1]
        week2_day2.exercises.append(PrescribedExercise(slot_id="rdl", exercise_id="romanian_deadlift", sets=3,
                                                       reps=8, reps_max=12, load=185, target_rpe=8))
        updated, _, notes = advance_program("strength", program, full_week1())
        assert updated.adjusted_through == 1
        assert rx(updated, 2, "rdl").load == 185
        assert not any("rdl" in n for n in notes)


    def test_skipped_lift_progresses_from_the_logged_weight(self):
        """Bench earned +5 in week 1, skipped in week 2: week 3's 190 is checked against the 185 logged."""
        program = make_program(weeks=3)
        rx(program, 2, "bench").load = None
        logs = full_week1(bench_reps=7) + sets_of("back_squat", 3, 225, 5, 8, day=2, slot_id="squat")
        updated, _, _ = advance_program("strength", program, logs)
        assert updated.adjusted_through == 2
        assert rx(updated, 3, "bench").load == 190


    def test_absurd_planned_load_in_a_new_slot_is_still_capped(self):
        """Reviewer finding: a planned 200 lb lateral raise in a new slot came back unchanged."""
        program = make_program(weeks=2)
        program.weeks[1].sessions[1].exercises.append(
            PrescribedExercise(slot_id="lateral_heavy", exercise_id="lateral_raise", sets=3, reps=12,
                               reps_max=15, load=200, target_rpe=9))
        updated, _, _ = advance_program("strength", program, full_week1())
        assert rx(updated, 2, "lateral_heavy").load == 20

    def test_changes_only_report_loads_that_survive_the_guards(self):
        """Reviewer finding: 'bench: ? -> 5 lb' was reported although the guard left it for the lifter."""
        program = make_program(weeks=2)
        rx(program, 1, "bench").load = None
        rx(program, 2, "bench").load = None
        logs = [l for l in full_week1() if l.exercise_id != "bench_press"] + bench_sets(1, 0, 7, 8)
        updated, changes, _ = advance_program("strength", program, logs)
        final = rx(updated, 2, "bench").load
        reported = [c for c in changes if " bench:" in c]
        assert reported == ([] if final is None else [f"W2 D1 bench: ? -> {final:g} lb"])


class TestLastLoggedLoads:
    def test_heaviest_working_set_of_the_latest_week(self):
        logs = bench_sets(1, 200, 5, 8) + bench_sets(2, 185, 5, 8) + bench_sets(2, 190, 3, 9, sets=1)
        assert last_logged_loads(make_program(), logs) == {"bench_press": 190}

    def test_heaviest_set_wins_whatever_order_it_was_logged_in(self):
        logs = bench_sets(2, 190, 3, 9, sets=1) + bench_sets(2, 185, 5, 8)
        assert last_logged_loads(make_program(), logs) == {"bench_press": 190}

    def test_ignores_warmups_failed_sets_and_other_blocks(self):
        program = make_program()
        logs = (sets_of("bench_press", 1, 135, 5, None, set_type="warmup")
                + [l.model_copy(update={"completed": False, "reps": 0}) for l in bench_sets(1, 225, 5, 8)]
                + [l.model_copy(update={"block": 2}) for l in bench_sets(1, 250, 5, 8)])
        assert last_logged_loads(program, logs) == {}


class TestGuardEnforcesDoubleProgression:
    def decision(self, step, load):
        return {(1, "bench"): SlotMetrics(slot_id="bench", day=1, exposures=[], plateau=False, regressing=False,
                                          in_progress=False, next_step=step, next_step_reason="",
                                          suggested_load=load, suggested_reps=5)}

    def test_no_weight_increase_without_add_load(self):
        """Reviewer m5: the AI can't add weight just because a session went well."""
        prev, proposed = make_program(weeks=1).weeks[0], make_program(weeks=1).weeks[0]
        proposed = proposed.model_copy(deep=True, update={"week": 2})
        rx_bench = next(e for e in proposed.sessions[0].exercises if e.slot_id == "bench")
        rx_bench.load = 190
        result = guard_week(proposed, "strength", "lb", previous=prev, slot_metrics=self.decision("add_reps", 185))
        assert next(e for e in result.week.sessions[0].exercises if e.slot_id == "bench").load == 185
        assert any("top of the rep range" in n for n in result.notes)

    def test_increase_allowed_up_to_engine_suggestion(self):
        prev = make_program(weeks=1).weeks[0]
        proposed = prev.model_copy(deep=True, update={"week": 2})
        next(e for e in proposed.sessions[0].exercises if e.slot_id == "bench").load = 195
        result = guard_week(proposed, "strength", "lb", previous=prev, slot_metrics=self.decision("add_load", 190))
        assert next(e for e in result.week.sessions[0].exercises if e.slot_id == "bench").load == 190
