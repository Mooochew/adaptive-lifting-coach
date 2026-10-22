"""The AI reviews the engine's baseline program by proposing bounded edits (see docs/phase3-plan.md)."""

import pytest

from app.exercises import get_exercise
from app.guards import weekly_muscle_sets
from app.review import ProgramReview, ProposedEdit, allowed_alternatives, review_program
from app.schemas import Profile
from app.templates import build_template

FULL_GYM = ["barbell", "dumbbell", "machine", "cable", "kettlebell", "bodyweight"]


def lifter(**kw) -> Profile:
    """4-day upper/lower, full gym: Day 1 bench/row/lateral/pushdown, Day 3 ohp/pulldown/incline/curl."""
    return Profile(**{"goal": "hypertrophy", "experience": "intermediate", "days_per_week": 4,
                      "session_minutes": 90, "equipment": FULL_GYM, **kw})


SHOULDER = "left shoulder hurts on overhead pressing"


def edit(kind, reason="Fits this lifter better.", **kw) -> ProposedEdit:
    fields = {"day": None, "slot_id": None, "new_exercise_id": None, "muscle": None, "sets_change": None,
              "reps": None, "reps_max": None, "target_rpe": None, "weekly_sets": None, "for_injury": False}
    return ProposedEdit(kind=kind, reason=reason, **{**fields, **kw})


def review(*edits, summary="Solid plan; small tweaks.") -> ProgramReview:
    return ProgramReview(summary=summary, edits=list(edits))


def slot(program, week, day, slot_id):
    w = next(w for w in program.weeks if w.week == week)
    s = next(s for s in w.sessions if s.day == day)
    return next((e for e in s.exercises if e.slot_id == slot_id), None)


def problems(outcome) -> str:
    return " | ".join(outcome.violations)


class TestNoEdits:
    def test_no_edits_gives_exactly_the_baseline(self):
        p = lifter()
        outcome = review_program(review(), p)
        assert outcome.program == build_template(p)[0]
        assert outcome.applied == [] and outcome.violations == []


class TestSwap:
    def test_template_alternative_is_allowed_and_carries_through_the_block(self):
        outcome = review_program(review(edit("swap", day=3, slot_id="ohp", new_exercise_id="db_shoulder_press")),
                                 lifter())
        assert outcome.violations == []
        assert {slot(outcome.program, w, 3, "ohp").exercise_id for w in range(1, 6)} == {"db_shoulder_press"}
        assert outcome.applied[0].change == "Standing Overhead Press → Seated Dumbbell Shoulder Press"

    def test_non_alternative_is_rejected_with_the_allowed_options(self):
        outcome = review_program(review(edit("swap", day=1, slot_id="bench", new_exercise_id="dip")), lifter())
        assert "dip" in problems(outcome) and "db_bench_press" in problems(outcome)

    def test_injury_swap_may_pick_any_exercise_for_the_same_muscle(self):
        outcome = review_program(
            review(edit("swap", day=1, slot_id="bench", new_exercise_id="dip", for_injury=True)),
            lifter(injuries=SHOULDER))
        assert outcome.violations == [] and slot(outcome.program, 1, 1, "bench").exercise_id == "dip"

    def test_injury_swap_must_still_train_the_same_muscle(self):
        outcome = review_program(
            review(edit("swap", day=1, slot_id="bench", new_exercise_id="leg_curl", for_injury=True)),
            lifter(injuries=SHOULDER))
        assert "primary muscle" in problems(outcome)

    def test_injury_edit_needs_an_injury_on_the_profile(self):
        outcome = review_program(
            review(edit("swap", day=1, slot_id="bench", new_exercise_id="dip", for_injury=True)), lifter())
        assert "injur" in problems(outcome)

    def test_equipment_must_be_available(self):
        p = lifter(equipment=["barbell", "dumbbell", "bodyweight"], days_per_week=3)
        outcome = review_program(review(edit("swap", day=1, slot_id="squat", new_exercise_id="leg_press")), p)
        assert "equipment" in problems(outcome)

    def test_added_accessory_alternatives_train_the_same_muscle(self):
        """x_face_pull (rear delts) must not list rows just because both appear among upper-back accessories."""
        p = lifter()
        session = build_template(p)[0].weeks[0].sessions[0]
        face_pull = next(e for e in session.exercises if e.slot_id == "x_face_pull")
        assert allowed_alternatives("x_face_pull", face_pull.exercise_id, session, set(FULL_GYM)) == []
        outcome = review_program(review(edit("swap", day=1, slot_id="x_face_pull", new_exercise_id="db_row")), p)
        assert "isn't a listed alternative" in problems(outcome)

    def test_cannot_swap_to_an_exercise_already_in_that_session(self):
        # Day 4 has leg press and Bulgarian split squats; the split squat is a listed leg-press alternative
        outcome = review_program(review(edit("swap", day=4, slot_id="legpress",
                                             new_exercise_id="bulgarian_split_squat")), lifter())
        assert "already" in problems(outcome)


class TestSetsRepsEffort:
    def test_one_set_less_is_kept_not_refilled(self):
        p = lifter()
        before = slot(build_template(p)[0], 1, 1, "bench").sets
        outcome = review_program(review(edit("sets", day=1, slot_id="bench", sets_change=-1)), p)
        assert outcome.violations == [] and slot(outcome.program, 1, 1, "bench").sets == before - 1

    def test_more_than_one_set_needs_an_injury(self):
        outcome = review_program(review(edit("sets", day=1, slot_id="bench", sets_change=-2)), lifter())
        assert "±1" in problems(outcome)
        ok = review_program(review(edit("sets", day=1, slot_id="bench", sets_change=-2, for_injury=True)),
                            lifter(injuries=SHOULDER))
        assert ok.violations == []

    def test_rep_range_within_limits(self):
        ok = review_program(review(edit("reps", day=1, slot_id="bench", reps=8, reps_max=12)), lifter())
        assert ok.violations == [] and (slot(ok.program, 1, 1, "bench").reps, slot(ok.program, 1, 1, "bench").reps_max) == (8, 12)
        bad = review_program(review(edit("reps", day=1, slot_id="bench", reps=12, reps_max=8)), lifter())
        assert "reps" in problems(bad)
        too_high = review_program(review(edit("reps", day=1, slot_id="bench", reps=20, reps_max=40)), lifter())
        assert "5-30" in problems(too_high)

    def test_timed_exercise_reps_cannot_be_edited(self):
        outcome = review_program(review(edit("reps", day=4, slot_id="core", reps=8, reps_max=12)), lifter())
        assert "timed" in problems(outcome)

    def test_effort_within_range_and_lower_only_for_injury(self):
        ok = review_program(review(edit("effort", day=1, slot_id="bench", target_rpe=7)), lifter())
        assert ok.violations == [] and slot(ok.program, 1, 1, "bench").target_rpe == 7
        easy = review_program(review(edit("effort", day=1, slot_id="bench", target_rpe=6)), lifter())
        assert "RPE" in problems(easy)
        injured = review_program(review(edit("effort", day=1, slot_id="bench", target_rpe=6, for_injury=True)),
                                 lifter(injuries=SHOULDER))
        assert injured.violations == []


class TestRemove:
    def test_remove_needs_an_injury_and_the_engine_keeps_volume_healthy(self):
        assert "injur" in problems(review_program(review(edit("remove", day=3, slot_id="ohp")), lifter()))
        outcome = review_program(review(edit("remove", day=3, slot_id="ohp", for_injury=True)),
                                 lifter(injuries=SHOULDER))
        assert outcome.violations == []
        assert all(slot(outcome.program, w, 3, "ohp") is None for w in range(1, 6))


class TestVolume:
    def test_lowering_a_muscle_moves_its_starting_point_and_it_ramps_from_there(self):
        p = lifter(training_background="8 years lifting; high volume beats up my knees.")
        outcome = review_program(review(edit("volume", muscle="quads", weekly_sets=8)), p)
        assert outcome.violations == []
        weeks = {w.week: weekly_muscle_sets(w).get("quads", 0) for w in outcome.program.weeks}
        baseline = {w.week: weekly_muscle_sets(w).get("quads", 0) for w in build_template(p)[0].weeks}
        assert weeks[1] <= 8 < baseline[1]
        assert all(weeks[n] < baseline[n] for n in (1, 2, 3, 4))
        assert outcome.applied[0].change == "quads: 11 → 8 weekly sets in week 1"

    def test_raising_a_muscle(self):
        outcome = review_program(review(edit("volume", muscle="biceps", weekly_sets=12)), lifter())
        assert outcome.violations == []
        assert weekly_muscle_sets(outcome.program.weeks[0])["biceps"] >= 12

    @pytest.mark.parametrize("goal, sets, ok", [("hypertrophy", 6, True), ("hypertrophy", 14, True),
                                                ("hypertrophy", 5, False), ("hypertrophy", 15, False),
                                                ("strength", 4, True), ("strength", 3, False),
                                                ("strength", 13, False)])
    def test_band(self, goal, sets, ok):
        outcome = review_program(review(edit("volume", muscle="chest", weekly_sets=sets)), lifter(goal=goal))
        assert (not any("band" in v for v in outcome.violations)) == ok

    def test_only_targeted_muscles_and_at_most_three(self):
        assert "abs" in problems(review_program(review(edit("volume", muscle="abs", weekly_sets=8)), lifter()))
        four = review(*[edit("volume", muscle=m, weekly_sets=10) for m in ("chest", "lats", "quads", "biceps")])
        assert "3 volume" in problems(review_program(four, lifter()))


class TestBudget:
    def test_at_most_four_exercise_edits(self):
        edits = [edit("effort", day=d, slot_id=s, target_rpe=7)
                 for d, s in ((1, "bench"), (1, "row"), (3, "ohp"), (3, "pulldown"), (2, "squat"))]
        assert "4 exercise edits" in problems(review_program(review(*edits), lifter()))

    def test_one_edit_per_slot(self):
        outcome = review_program(review(edit("effort", day=1, slot_id="bench", target_rpe=7),
                                        edit("sets", day=1, slot_id="bench", sets_change=1)), lifter())
        assert "one edit per" in problems(outcome)

    def test_unknown_slot(self):
        assert "no slot" in problems(review_program(review(edit("sets", day=1, slot_id="nope", sets_change=1)),
                                                    lifter()))

    def test_reason_and_summary_lengths(self):
        assert "reason" in problems(review_program(review(edit("sets", "", day=1, slot_id="bench", sets_change=1)),
                                                   lifter()))
        assert "reason" in problems(review_program(
            review(edit("sets", "x" * 201, day=1, slot_id="bench", sets_change=1)), lifter()))
        assert "summary" in problems(review_program(review(summary="x" * 401), lifter()))

    def test_invalid_edits_change_nothing(self):
        p = lifter()
        outcome = review_program(review(edit("sets", day=1, slot_id="bench", sets_change=3)), p)
        assert outcome.violations and outcome.program == build_template(p)[0]

    def test_strength_goal_and_kg_work_too(self):
        p = lifter(goal="strength", units="kg")
        outcome = review_program(review(edit("swap", day=3, slot_id="ohp", new_exercise_id="db_shoulder_press")), p)
        assert outcome.violations == [] and outcome.program.units == "kg"


def test_applied_edits_name_the_exercises_not_the_models_words():
    outcome = review_program(review(edit("sets", "Rear delts are already well covered.", day=1, slot_id="row",
                                         sets_change=-1)), lifter())
    applied = outcome.applied[0]
    name = get_exercise("barbell_row").name
    assert applied.change.startswith(name) and applied.reason == "Rear delts are already well covered."
