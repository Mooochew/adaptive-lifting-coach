"""Iterative refinement: ask, check, send the problems back, try again; fall back if it never passes.

The "Iterative Refinement" prompt pattern as plain, testable code (no LLM needed):
  1. generate(feedback) -> candidate     feedback is [] on the first try, then the latest problems
  2. check(candidate)   -> Checked       the guards: fix what they can (notes), report the rest (violations)
  3. no violations -> done; otherwise ask again with the violations as feedback
  4. after `max_attempts`, use fallback() (the rule-based template), so the lifter always gets a program

What counts as what:
  - invalid output (not JSON, wrong shape, unknown exercise id: FEEDBACK_ERRORS) is the AI's
    mistake, so it becomes feedback too. `generate` should parse with Program.model_validate
    and raise InvalidOutput for anything else it can't use.
  - any other error from `generate` (network, API, timeout) can't be fixed by asking again:
    fall back right away.
  - an error from `check` or `fallback` is a bug in our own code: it propagates, never hidden.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Generic, TypeVar

from pydantic import ValidationError

from app.guards import UnknownExerciseError, guard_program
from app.rules import Goal
from app.schemas import LiftEstimate, Program

T = TypeVar("T")

MAX_ATTEMPTS = 3          # each attempt is a paid API call and a few seconds of waiting


class InvalidOutput(ValueError):
    """The AI's reply can't be used as-is (e.g. no JSON in it). Becomes feedback."""


FEEDBACK_ERRORS = (InvalidOutput, ValidationError, UnknownExerciseError, json.JSONDecodeError)


@dataclass
class Checked(Generic[T]):
    value: T
    notes: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)


@dataclass
class Refined(Generic[T]):
    value: T
    notes: list[str]
    attempts: list[list[str]]             # problems found on each attempt; [] = it passed
    used_fallback: bool = False
    fallback_reason: str | None = None    # safe to show the lifter
    error: str | None = None              # details of an outage, for server logs only


def describe_invalid(err: Exception) -> list[str]:
    """Readable feedback for invalid output, one line per problem: where and what, not the
    offending value itself (an unknown exercise id is named, since it is the problem)."""
    if isinstance(err, ValidationError):
        return [f"{'.'.join(str(p) for p in e['loc']) or 'output'}: {e['msg']}" for e in err.errors()]
    if isinstance(err, json.JSONDecodeError):
        return [f"reply is not valid JSON: {err.msg} (line {err.lineno}, column {err.colno})"]
    return [str(err)]


def refine(
    generate: Callable[[list[str]], T],
    check: Callable[[T], Checked[T]],
    fallback: Callable[[], T],
    max_attempts: int = MAX_ATTEMPTS,
) -> Refined[T]:
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    attempts: list[list[str]] = []
    feedback: list[str] = []
    for _ in range(max_attempts):
        try:
            candidate = generate(feedback)
        except FEEDBACK_ERRORS as err:
            feedback = describe_invalid(err)
            attempts.append(feedback)
            continue
        except Exception as err:      # outage, not a mistake the AI can fix
            return Refined(fallback(), [], attempts, used_fallback=True,
                           fallback_reason="AI unavailable", error=f"{type(err).__name__}: {err}")

        try:
            checked = check(candidate)
        except FEEDBACK_ERRORS as err:
            feedback = describe_invalid(err)
            attempts.append(feedback)
            continue
        if not checked.violations:
            attempts.append([])
            return Refined(checked.value, checked.notes, attempts)
        feedback = checked.violations
        attempts.append(feedback)

    return Refined(fallback(), [], attempts, used_fallback=True,
                   fallback_reason=f"still breaking the training rules after {max_attempts} attempts")


def check_program(
    goal: Goal,
    estimates: list[LiftEstimate] | None = None,
    last_loads: dict[str, float] | None = None,
) -> Callable[[Program], Checked[Program]]:
    """The check for an AI-generated program: every week through the guards, with the
    lifter's onboarding sets (`estimates`) and logged history (`last_loads`) as baselines."""
    def check(program: Program) -> Checked[Program]:
        result = guard_program(program, goal, estimates=estimates, last_loads=last_loads)
        return Checked(result.program, result.notes, result.violations)
    return check
