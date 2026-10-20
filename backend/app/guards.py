"""Safety rails applied to LLM output before it reaches the user.

The LLM chooses *what* to change; these functions make sure the numbers stay
inside the training rules:
  - fixable problems are clamped and reported as `notes`
    (load jumps, rep ranges, target RPE, a load silently set to 0)
  - problems that need re-planning are reported as `violations`
    (weekly sets per muscle over the cap or ramped too fast, missing loads),
    so the caller can ask the LLM to try again
"""

from collections import defaultdict

from pydantic import BaseModel

from app.exercises import get_exercise, is_known, muscle_credits
from app.rules import (
    MAX_WEEKLY_DECREASE,
    MAX_WEEKLY_INCREASE,
    MIN_LOAD_STEP,
    Goal,
    goal_rules,
    rules_for_exercise,
    target_rpe_range,
)
from app.schemas import Program, Units, Week
from app.training import round_to_increment


class UnknownExerciseError(ValueError):
    def __init__(self, ids: list[str]):
        super().__init__(f"Unknown exercise ids: {', '.join(ids)}")
        self.ids = ids


class GuardResult(BaseModel):
    week: Week
    notes: list[str]          # changes the guards made
    violations: list[str]     # problems the guards can't fix on their own


def unknown_exercise_ids(program: Program | Week) -> list[str]:
    weeks = program.weeks if isinstance(program, Program) else [program]
    found = {ex.exercise_id for w in weeks for s in w.sessions for ex in s.exercises}
    return sorted(i for i in found if not is_known(i))


def ensure_known_exercises(program: Program | Week) -> None:
    unknown = unknown_exercise_ids(program)
    if unknown:
        raise UnknownExerciseError(unknown)


def clamp_load(previous: float, proposed: float, units: Units) -> float:
    """Limit a week-over-week load change and round to a loadable weight.

    Always allows at least one minimum load step (5 lb / 2.5 kg), so light lifts can
    still progress (5% of 60 lb is less than one 5 lb jump).
    """
    if proposed <= 0:
        return 0.0
    if previous <= 0:
        return round_to_increment(proposed, units)

    inc = MIN_LOAD_STEP[units]
    upper = max(previous * (1 + MAX_WEEKLY_INCREASE), previous + inc)
    lower = min(previous * (1 - MAX_WEEKLY_DECREASE), previous - inc)
    bounded = min(max(proposed, lower), upper)

    rounded = round_to_increment(bounded, units)
    # Rounding must not push us back outside the bounds.
    if rounded > upper:
        rounded -= inc
    if rounded < lower:
        rounded += inc
    return max(rounded, 0.0)


def weekly_muscle_sets(week: Week) -> dict[str, float]:
    """Prescribed hard sets per muscle (primary 1, secondary 0.5)."""
    totals: dict[str, float] = defaultdict(float)
    for s in week.sessions:
        for ex in s.exercises:
            for muscle, credit in muscle_credits(ex.exercise_id).items():
                totals[muscle] += ex.sets * credit
    return dict(totals)


def _previous_loads(previous: Week | None) -> tuple[dict, dict]:
    by_slot: dict[tuple[int, str], tuple[str, float]] = {}
    by_exercise: dict[str, float] = {}
    if previous:
        for s in previous.sessions:
            for ex in s.exercises:
                by_slot[(s.day, ex.slot_id)] = (ex.exercise_id, ex.load)
                by_exercise[ex.exercise_id] = max(by_exercise.get(ex.exercise_id, 0.0), ex.load)
    return by_slot, by_exercise


def guard_week(
    proposed: Week,
    goal: Goal,
    units: Units,
    previous: Week | None = None,
    last_loads: dict[str, float] | None = None,
) -> GuardResult:
    """Clamp an LLM-proposed week to the training rules.

    `previous` is last week's prescription; `last_loads` is the most recent
    logged working load per exercise, used when a lift has no previous slot.
    """
    ensure_known_exercises(proposed)
    rules_for_goal = goal_rules(goal)
    week = proposed.model_copy(deep=True)
    notes: list[str] = []
    violations: list[str] = []
    prev_slot, prev_exercise = _previous_loads(previous)
    last_loads = last_loads or {}

    for session in week.sessions:
        for ex in session.exercises:
            info = get_exercise(ex.exercise_id)
            rules = rules_for_exercise(goal, info)
            where = f"Day {session.day} {ex.slot_id} ({ex.exercise_id})"

            # Rep range
            if not info.timed:
                lo, hi = int(rules.reps_limit.low), int(rules.reps_limit.high)
                reps = min(max(ex.reps, lo), hi)
                reps_max = None if ex.reps_max is None else min(max(ex.reps_max, reps), hi)
                if (reps, reps_max) != (ex.reps, ex.reps_max):
                    notes.append(f"{where}: reps {ex.reps}-{ex.reps_max or ex.reps} -> "
                                 f"{reps}-{reps_max or reps} (limit {lo}-{hi}).")
                    ex.reps, ex.reps_max = reps, reps_max

            # Target RPE (deload weeks may be easier by the deload RIR)
            rpe_range = target_rpe_range(rules)
            rpe_low = rpe_range.low - (rules_for_goal.deload.rir_add if week.is_deload else 0)
            rpe = min(max(ex.target_rpe, rpe_low), rpe_range.high)
            if rpe != ex.target_rpe:
                notes.append(f"{where}: target RPE {ex.target_rpe:g} -> {rpe:g} "
                             f"(allowed {rpe_low:g}-{rpe_range.high:g}).")
                ex.target_rpe = rpe

            # Load: compare against the same slot, else the same lift on any day
            # last week, else the last logged load.
            slot = prev_slot.get((session.day, ex.slot_id))
            before = (
                slot[1] if slot and slot[0] == ex.exercise_id
                else prev_exercise.get(ex.exercise_id, last_loads.get(ex.exercise_id, 0.0))
            )
            bodyweight = info.equipment == "bodyweight"

            if ex.load <= 0 and not bodyweight:
                if before > 0:
                    notes.append(f"{where}: load 0 is not allowed for a loaded lift; kept {before:g} {units}.")
                    ex.load = before
                else:
                    violations.append(f"{where}: no load given and no previous load to fall back on.")
                continue

            clamped = clamp_load(before, ex.load, units)
            if clamped != ex.load:
                reason = f"capped vs {before:g}" if before > 0 else "rounded to nearest loadable weight"
                notes.append(f"{where}: load {ex.load:g} -> {clamped:g} {units} ({reason}).")
                ex.load = clamped

    # Weekly volume per muscle
    if not week.is_deload:
        volume = rules_for_goal.volume
        current = weekly_muscle_sets(week)
        before_sets = weekly_muscle_sets(previous) if previous and not previous.is_deload else None
        for muscle, sets in sorted(current.items()):
            if sets > volume.max:
                violations.append(f"{muscle}: {sets:g} weekly sets exceeds the max of {volume.max:g}.")
            if before_sets is not None:
                increase = sets - before_sets.get(muscle, 0.0)
                if increase > volume.ramp.high:
                    violations.append(
                        f"{muscle}: +{increase:g} sets vs last week exceeds the ramp of +{volume.ramp.high:g}."
                    )

    return GuardResult(week=week, notes=notes, violations=violations)
