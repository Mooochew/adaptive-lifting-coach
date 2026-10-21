import json

import pytest
from pydantic import ValidationError

from app.guards import UnknownExerciseError
from app.refine import Checked, InvalidOutput, check_program, refine
from app.schemas import LiftEstimate, Program
from tests.conftest import make_program


class FakeAI:
    """Scripted generator: returns (or raises) one item per call and records the feedback it got."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.feedback: list[list[str]] = []

    def __call__(self, feedback: list[str]):
        self.feedback.append(feedback)
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


def check(candidate: str) -> Checked[str]:
    """Toy checker: "bad..." breaks a rule, "fixable" gets cleaned up with a note."""
    if candidate.startswith("bad"):
        return Checked(candidate, notes=[], violations=[f"{candidate} breaks a rule"])
    if candidate == "fixable":
        return Checked("fixed", notes=["cleaned up"], violations=[])
    return Checked(candidate, notes=[], violations=[])


def fallback() -> str:
    return "template"


def validation_error() -> ValidationError:
    try:
        Program.model_validate({"weeks": []})
    except ValidationError as err:
        return err
    raise AssertionError("expected a validation error")


def test_first_answer_accepted():
    ai = FakeAI("good")
    result = refine(ai, check, fallback)
    assert (result.value, result.used_fallback, len(result.attempts)) == ("good", False, 1)
    assert ai.feedback == [[]]


def test_guard_fixes_are_kept_with_notes():
    result = refine(FakeAI("fixable"), check, fallback)
    assert result.value == "fixed" and result.notes == ["cleaned up"]


def test_violations_are_sent_back_and_fixed():
    ai = FakeAI("bad week", "good")
    result = refine(ai, check, fallback)
    assert result.value == "good" and not result.used_fallback
    assert ai.feedback == [[], ["bad week breaks a rule"]]
    assert result.attempts == [["bad week breaks a rule"], []]


def test_feedback_is_only_the_latest_problems():
    ai = FakeAI("bad 1", "bad 2", "good")
    refine(ai, check, fallback)
    assert ai.feedback[2] == ["bad 2 breaks a rule"]


def test_falls_back_after_max_attempts():
    ai = FakeAI("bad 1", "bad 2", "bad 3", "good")
    result = refine(ai, check, fallback, max_attempts=3)
    assert result.value == "template" and result.used_fallback
    assert len(ai.feedback) == 3                     # never asked a 4th time
    assert "3 attempts" in result.fallback_reason


def test_single_attempt():
    result = refine(FakeAI("bad"), check, fallback, max_attempts=1)
    assert result.used_fallback and len(result.attempts) == 1


@pytest.mark.parametrize("invalid", [
    validation_error(),
    InvalidOutput("no JSON object in the reply"),
    UnknownExerciseError(["benchpress"]),
    json.JSONDecodeError("Expecting value", "oops", 0),
])
def test_invalid_output_is_feedback_not_a_crash(invalid):
    """Malformed AI output (bad JSON or shape, unknown exercise) gets another chance."""
    ai = FakeAI(invalid, "good")
    result = refine(ai, check, fallback)
    assert result.value == "good"
    assert ai.feedback[1] and all(isinstance(line, str) for line in ai.feedback[1])


def test_validation_feedback_names_the_field():
    ai = FakeAI(validation_error(), "good")
    refine(ai, check, fallback)
    assert any("weeks" in line for line in ai.feedback[1])


def test_validation_feedback_never_repeats_the_offending_values():
    """Feedback goes back into the prompt: say where and what, not the model's own text."""
    try:
        Program.model_validate({"weeks": [{"week": 1, "sessions": [{"day": 1, "focus": "IGNORE RULES " * 10,
                                                                    "exercises": []}]}]})
    except ValidationError as err:
        invalid = err
    ai = FakeAI(invalid, "good")
    refine(ai, check, fallback)
    assert ai.feedback[1] and not any("IGNORE" in line for line in ai.feedback[1])


@pytest.mark.parametrize("outage", [RuntimeError("API unavailable"), ValueError("bad API key format"),
                                    TimeoutError("timed out")])
def test_other_errors_fall_back_immediately(outage):
    """An outage (network, API error) isn't fixable by asking again: use the template."""
    ai = FakeAI(outage, "good")
    result = refine(ai, check, fallback)
    assert result.value == "template" and result.used_fallback
    assert len(ai.feedback) == 1
    assert result.fallback_reason == "AI unavailable"         # no internals for the client


def test_a_rule_problem_raised_by_the_checker_is_feedback():
    """e.g. guard_program raising UnknownExerciseError for an id that slipped past parsing."""
    calls = []

    def strict_check(candidate):
        calls.append(candidate)
        if len(calls) == 1:
            raise UnknownExerciseError(["benchpress"])
        return Checked(candidate)

    ai = FakeAI("first", "second")
    result = refine(ai, strict_check, fallback)
    assert result.value == "second" and ai.feedback[1] == ["Unknown exercise ids: benchpress"]


def test_a_bug_in_the_checker_is_not_hidden_as_ai_feedback():
    def broken_check(candidate):
        raise TypeError("bug in our own code")

    with pytest.raises(TypeError):
        refine(FakeAI("good", "good", "good"), broken_check, fallback)


def test_a_bug_in_the_fallback_is_not_hidden():
    def broken_fallback():
        raise KeyError("template bug")

    with pytest.raises(KeyError):
        refine(FakeAI("bad", "bad", "bad"), check, broken_fallback)


def test_interrupts_are_never_swallowed():
    with pytest.raises(KeyboardInterrupt):
        refine(FakeAI(KeyboardInterrupt()), check, fallback)


def test_max_attempts_must_be_positive():
    with pytest.raises(ValueError):
        refine(FakeAI(), check, fallback, max_attempts=0)


class TestWithTheRealGuards:
    """The path Claude's output will take: raw JSON-like data -> Program -> guard_program."""

    ESTIMATES = [LiftEstimate(exercise_id="bench_press", weight=215, reps=5),
                 LiftEstimate(exercise_id="lateral_raise", weight=25, reps=12),
                 LiftEstimate(exercise_id="back_squat", weight=265, reps=5)]

    def generate_from(self, ai):
        return lambda feedback: Program.model_validate(ai(feedback))

    def test_bad_shape_then_volume_violation_then_valid(self):
        too_much = make_program(weeks=1).model_dump()
        too_much["weeks"][0]["sessions"][0]["exercises"][0]["sets"] = 10
        too_much["weeks"][0]["sessions"][0]["exercises"].append(
            {**too_much["weeks"][0]["sessions"][0]["exercises"][0], "slot_id": "bench2"})
        good = make_program(weeks=1).model_dump()
        ai = FakeAI({"weeks": "not a list"}, too_much, good)

        result = refine(self.generate_from(ai), check_program("hypertrophy", self.ESTIMATES),
                        lambda: make_program(weeks=1))

        assert not result.used_fallback and len(result.attempts) == 3
        assert any("weeks" in line for line in ai.feedback[1])
        assert any("chest" in line and "one session" in line for line in ai.feedback[2])
        assert result.value.weeks[0].sessions[0].exercises[0].load == 185

    def test_invented_weights_are_fixed_without_a_retry(self):
        program = make_program(weeks=1).model_dump()
        program["weeks"][0]["sessions"][1]["exercises"][0]["load"] = 900    # squat: AI made it up
        ai = FakeAI(program)
        result = refine(self.generate_from(ai), check_program("strength", self.ESTIMATES),
                        lambda: make_program(weeks=1))
        squat = result.value.weeks[0].sessions[1].exercises[0]
        assert len(ai.feedback) == 1 and squat.load == 250
        assert any("squat" in n for n in result.notes)

    def test_logged_history_counts_for_a_later_block(self):
        """Block 2 written by the AI: lifts without an onboarding set keep weights the lifter logged."""
        program = make_program(weeks=1).model_dump()
        ai = FakeAI(program)
        result = refine(self.generate_from(ai),
                        check_program("hypertrophy", last_loads={"lateral_raise": 20, "back_squat": 225,
                                                                 "bench_press": 185}),
                        lambda: make_program(weeks=1))
        lateral = result.value.weeks[0].sessions[0].exercises[1]
        assert lateral.load == 20 and result.notes == []
