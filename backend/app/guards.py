"""Safety rails applied to LLM output before it reaches the user.

The LLM chooses *what* to change; these functions make sure the numbers stay
inside the training rules:
  - fixable problems are clamped and reported as `notes`
    (load jumps, rep ranges, target RPE, a load silently set to 0, and loads for
    lifts with no history: capped by the lifter's recent set, else left for the
    lifter to pick, so the AI can never invent a weight)
  - problems that need re-planning are reported as `violations`
    (sets per muscle over the session or weekly cap, or ramped too fast),
    so the caller can ask the LLM to try again (see refine.py)
"""

from collections import defaultdict

from pydantic import BaseModel

from app.exercises import ExerciseInfo, get_exercise, is_known, muscle_credits
from app.rules import (
    MAX_WEEKLY_DECREASE,
    MAX_SESSION_SETS_PER_MUSCLE,
    MAX_WEEKLY_INCREASE,
    MIN_HARD_WEEKS_BEFORE_DELOAD,
    MIN_LOAD_STEP,
    Goal,
    goal_rules,
    rules_for_exercise,
    target_rpe_range,
)
from app.schemas import LiftEstimate, PrescribedExercise, Program, Session, SlotMetrics, Units, Week
from app.training import load_from_recent_set, round_to_increment


class UnknownExerciseError(ValueError):
    def __init__(self, ids: list[str]):
        super().__init__(f"Unknown exercise ids: {', '.join(ids)}")
        self.ids = ids


class GuardResult(BaseModel):
    week: Week
    notes: list[str]          # changes the guards made
    violations: list[str]     # problems the guards can't fix on their own


class ProgramGuardResult(BaseModel):
    program: Program
    notes: list[str]          # prefixed with the week, e.g. "W2: ..."
    violations: list[str]


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


def no_history_load(ex: PrescribedExercise, info: ExerciseInfo, estimate: LiftEstimate | None,
                    units: Units) -> tuple[float | None, str | None]:
    """Load for a lift with no previous or logged weight, and why it changed (None = unchanged).

    The only trusted source is the lifter's own recent set: allow up to what it supports
    for this prescription plus one load step, else cap at what it supports. With no
    recent set, the load stays unknown and the lifter picks it on day one.
    """
    proposed = ex.load or 0.0
    step = MIN_LOAD_STEP[units]
    if info.equipment == "bodyweight":
        if proposed > 0:
            return 0.0, f"no history for this lift; extra load {proposed:g} -> 0 {units}"
        return proposed, None
    if estimate is None or info.timed:
        return None, f"no history for this lift; load {proposed:g} -> none (lifter picks a working weight)"

    supported = load_from_recent_set(estimate.weight, estimate.reps, ex.reps, ex.target_rpe, units)
    if supported < step:
        return None, (f"no history for this lift and the recent set is too light to set a load; "
                      f"load {proposed:g} -> none (lifter picks a working weight)")
    if proposed <= 0:
        return supported, f"load {proposed:g} -> {supported:g} {units} (set from your recent set)"
    if proposed > supported + step:
        return supported, f"load {proposed:g} -> {supported:g} {units} (capped at what your recent set supports)"
    return _rounded(proposed, units)


def borrowed_load(ex: PrescribedExercise, borrowed: float, bodyweight: bool,
                  units: Units) -> tuple[float | None, str | None]:
    """Load for a slot with no load of its own last week, when the same lift has one elsewhere
    (another day last week, or the last logged load). That only caps the load from above:
    a light or bodyweight day must never be pushed up to a heavy day's weight."""
    proposed = ex.load or 0.0
    if proposed <= 0:
        if bodyweight:
            return proposed, None
        return None, f"load 0 is not allowed for a loaded lift; load -> none (lifter picks a working weight)"
    if proposed > borrowed:
        capped = clamp_load(borrowed, proposed, units)
        if capped != proposed:
            return capped, f"load {proposed:g} -> {capped:g} {units} (capped vs {borrowed:g} for this lift)"
        return proposed, None
    return _rounded(proposed, units, allow_zero=bodyweight)


def _bodyweight_step(proposed: float, units: Units) -> tuple[float, str | None]:
    """A bodyweight lift done at bodyweight last week may add at most one load step."""
    step = MIN_LOAD_STEP[units]
    if proposed > step:
        return step, f"extra load {proposed:g} -> {step:g} {units} (one step above last week's bodyweight)"
    return _rounded(proposed, units, allow_zero=True)


def _earned(load: float, before: float, slot_metrics: dict[tuple[int, str], SlotMetrics] | None,
            day: int, slot_id: str, units: Units, where: str, notes: list[str]) -> float:
    """Double progression: weight above `before` only where the engine's decision for this
    slot is "add_load", and never above its suggested load. Without the engine's decisions
    (a brand-new program) only the load caps apply. Not used for deload weeks, which never
    become the baseline for the week after."""
    if slot_metrics is None or load <= before:
        return load
    decision = slot_metrics.get((day, slot_id))
    earned = decision is not None and decision.next_step == "add_load" and decision.suggested_load
    allowed = min(load, decision.suggested_load) if earned else before
    if load > allowed:
        why = ("capped at the engine's suggested load" if earned else
               "weight only goes up once every set reaches the top of the rep range")
        notes.append(f"{where}: load {load:g} -> {allowed:g} {units} ({why}).")
        return allowed
    return load


def _rounded(load: float, units: Units, allow_zero: bool = False) -> tuple[float, str | None]:
    """Round to a loadable weight; a loaded lift never rounds down to 0."""
    rounded = round_to_increment(load, units)
    if not allow_zero:
        rounded = max(rounded, MIN_LOAD_STEP[units])
    if rounded != load:
        return rounded, f"load {load:g} -> {rounded:g} {units} (rounded to nearest loadable weight)"
    return load, None


def session_muscle_sets(session: Session) -> dict[str, float]:
    """Prescribed hard sets per muscle in one session (primary 1, secondary 0.5)."""
    totals: dict[str, float] = defaultdict(float)
    for ex in session.exercises:
        for muscle, credit in muscle_credits(ex.exercise_id).items():
            totals[muscle] += ex.sets * credit
    return dict(totals)


def weekly_muscle_sets(week: Week) -> dict[str, float]:
    """Prescribed hard sets per muscle (primary 1, secondary 0.5)."""
    totals: dict[str, float] = defaultdict(float)
    for s in week.sessions:
        for muscle, sets in session_muscle_sets(s).items():
            totals[muscle] += sets
    return dict(totals)


def _previous_loads(previous: Week | None) -> tuple[dict, dict]:
    """(day, slot_id) -> (exercise_id, load or None), and exercise_id -> heaviest known load."""
    by_slot: dict[tuple[int, str], tuple[str, float | None]] = {}
    by_exercise: dict[str, float] = {}
    if previous:
        for s in previous.sessions:
            for ex in s.exercises:
                by_slot[(s.day, ex.slot_id)] = (ex.exercise_id, ex.load)
                by_exercise[ex.exercise_id] = max(by_exercise.get(ex.exercise_id, 0.0), ex.load or 0.0)
    return by_slot, by_exercise


def guard_week(
    proposed: Week,
    goal: Goal,
    units: Units,
    previous: Week | None = None,
    last_loads: dict[str, float] | None = None,
    slot_metrics: dict[tuple[int, str], SlotMetrics] | None = None,
    estimates: list[LiftEstimate] | None = None,
    approved_loads: dict[tuple[int, str], float] | None = None,
) -> GuardResult:
    """Clamp an LLM-proposed week to the training rules.

    Load baselines, most trusted first:
      `previous`        last week's prescription; a slot's own load there caps changes both ways
      `last_loads`      the most recent logged working load per exercise, and the same lift on
                        another day last week: these only cap a load from above
      `approved_loads`  (day, slot_id) -> a load already approved when the program was made;
                        kept only for a lift with no history anywhere (e.g. one new mid-block),
                        never in place of the checks above
      `estimates`       the lifter's recent sets (from the profile), for a lift with no history
    `slot_metrics` ((day, slot_id) -> the engine's decision for that slot) enforces
    double progression: weight may only rise where the engine says "add_load",
    and never above the engine's suggested load.
    """
    ensure_known_exercises(proposed)
    rules_for_goal = goal_rules(goal)
    week = proposed.model_copy(deep=True)
    notes: list[str] = []
    violations: list[str] = []
    prev_slot, prev_exercise = _previous_loads(previous)
    last_loads = last_loads or {}
    approved_loads = approved_loads or {}
    estimate_for = {e.exercise_id: e for e in estimates or []}

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

            # Load: this slot's own load last week is the baseline. Without one, see
            # borrowed_load / approved_loads / no_history_load.
            slot = prev_slot.get((session.day, ex.slot_id))
            own = slot[1] if slot and slot[0] == ex.exercise_id else None
            bodyweight = info.equipment == "bodyweight"

            if not own:
                if ex.load is None:
                    continue      # unknown stays unknown; never filled in from another slot
                # The heavier of the same lift elsewhere last week and what the lifter last logged.
                borrowed = max(prev_exercise.get(ex.exercise_id, 0.0), last_loads.get(ex.exercise_id, 0.0))
                if borrowed > 0:
                    ex.load, why = borrowed_load(ex, borrowed, bodyweight, units)
                elif bodyweight and own == 0:
                    ex.load, why = _bodyweight_step(ex.load, units)     # done at bodyweight last week
                elif approved_loads.get((session.day, ex.slot_id)) == ex.load:
                    why = None    # a lift with no history anywhere, whose load was approved with the plan
                else:
                    ex.load, why = no_history_load(ex, info, estimate_for.get(ex.exercise_id), units)
                if why:
                    notes.append(f"{where}: {why}.")
                if borrowed > 0 and ex.load is not None and not week.is_deload:
                    ex.load = _earned(ex.load, borrowed, slot_metrics, session.day, ex.slot_id, units,
                                      where, notes)
                continue

            before = own
            if ex.load is None:
                notes.append(f"{where}: load missing; kept {before:g} {units}.")
                ex.load = before
                continue

            if ex.load <= 0 and not bodyweight:
                notes.append(f"{where}: load 0 is not allowed for a loaded lift; kept {before:g} {units}.")
                ex.load = before
                continue

            clamped = clamp_load(before, ex.load, units)
            if clamped != ex.load:
                notes.append(f"{where}: load {ex.load:g} -> {clamped:g} {units} (capped vs {before:g}).")
                ex.load = clamped

            if not week.is_deload:      # deload loads come from the engine's suggestion minus the cut
                ex.load = _earned(ex.load, before, slot_metrics, session.day, ex.slot_id, units, where, notes)

    # Per-session volume per muscle (deload weeks too: calling a week a deload is no way around it)
    for session in week.sessions:
        for muscle, sets in sorted(session_muscle_sets(session).items()):
            if sets > MAX_SESSION_SETS_PER_MUSCLE:
                violations.append(
                    f"Day {session.day} {muscle}: {sets:g} sets in one session exceeds "
                    f"{MAX_SESSION_SETS_PER_MUSCLE:g}; move some to another day."
                )

    # Weekly volume per muscle: the cap always; then hard weeks ramp gradually and
    # deload weeks may only cut volume vs the last hard week.
    volume = rules_for_goal.volume
    current = weekly_muscle_sets(week)
    before_sets = weekly_muscle_sets(previous) if previous and not previous.is_deload else None
    for muscle, sets in sorted(current.items()):
        if sets > volume.max:
            violations.append(f"{muscle}: {sets:g} weekly sets exceeds the max of {volume.max:g}.")
        if before_sets is None:
            continue
        last = before_sets.get(muscle, 0.0)
        if week.is_deload and sets > last:
            violations.append(f"{muscle}: {sets:g} sets in a deload week is more than the {last:g} "
                              f"of the last hard week; a deload cuts volume.")
        elif not week.is_deload and sets - last > volume.ramp.high:
            violations.append(
                f"{muscle}: +{sets - last:g} sets vs last week exceeds the ramp of +{volume.ramp.high:g}."
            )

    return GuardResult(week=week, notes=notes, violations=violations)


def guard_program(
    program: Program,
    goal: Goal,
    estimates: list[LiftEstimate] | None = None,
    last_loads: dict[str, float] | None = None,
) -> ProgramGuardResult:
    """Guard every week in order, each against the last hard week before it.

    Deload weeks are checked but never become the baseline (loads returning after
    a deload aren't a jump), and each must follow enough hard weeks, so a program
    can't call ordinary weeks "deloads" to get around the volume rules.
    Raises UnknownExerciseError for ids not in the library.
    """
    ensure_known_exercises(program)
    weeks: list[Week] = []
    notes: list[str] = []
    violations: list[str] = []
    previous: Week | None = None
    hard_weeks = 0
    for week in sorted(program.weeks, key=lambda w: w.week):
        if week.is_deload:
            if hard_weeks < MIN_HARD_WEEKS_BEFORE_DELOAD:
                violations.append(f"W{week.week}: a deload needs at least {MIN_HARD_WEEKS_BEFORE_DELOAD} "
                                  f"hard weeks before it (found {hard_weeks}).")
            hard_weeks = 0
        else:
            hard_weeks += 1
        result = guard_week(week, goal, program.units, previous=previous,
                            last_loads=last_loads, estimates=estimates)
        notes += [f"W{week.week}: {n}" for n in result.notes]
        violations += [f"W{week.week}: {v}" for v in result.violations]
        weeks.append(result.week)
        if not week.is_deload:
            previous = result.week
    return ProgramGuardResult(program=program.model_copy(update={"weeks": weeks}),
                              notes=notes, violations=violations)
