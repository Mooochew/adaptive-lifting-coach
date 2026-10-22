"""AI program review: the engine writes the program, the AI reviews it like a coach.

The AI never designs a program. It sees the engine's baseline and the lifter's profile and
proposes a few bounded edits, each with a reason (see docs/phase3-plan.md):

  swap     replace a slot's exercise: a template alternative, or (for an injury) any exercise
           with available equipment that trains the same primary muscle
  sets     +/-1 set on one exercise (an injury may reduce more)
  reps     a rep range within the rules' limits for that lift
  effort   a target RPE within the allowed range (an injury may go lower)
  remove   drop an exercise (injury only)
  volume   set a muscle's week-1 weekly sets within the goal's start band

Edits target week 1's slots. The engine keeps every muscle at its target around them (edited
slots stay as edited), then regenerates weeks 2-4 and the deload, and runs every guard. Anything
outside the edit budget becomes a violation, which refine.py sends back to the AI as feedback.
Any violation means the lifter gets the untouched baseline, never a half-edited program.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.exercises import EXERCISES, ExerciseInfo, Muscle, get_exercise
from app.guards import weekly_muscle_sets
from app.rules import VOLUME_TARGET_MUSCLES, goal_rules, rules_for_exercise, target_rpe_range
from app.schemas import Model, Profile, Program, Session, Week
from app.templates import (
    ACCESSORIES,
    MAX_SETS_PER_EXERCISE,
    SLOTS,
    _session_minutes,
    build_week_one,
    expand_block,
    fill_volume,
    make_prescription,
    reduce_volume,
)

MAX_EXERCISE_EDITS = 4       # not counting injury edits
MAX_VOLUME_EDITS = 3
MAX_EDITS = 12               # everything, a sanity cap
MAX_REASON = 200
MAX_SUMMARY = 400
INJURY_RPE_DROP = 2          # an injury edit may set effort this far below the normal range

ExerciseId = Literal[tuple(e.id for e in EXERCISES)]
EditKind = Literal["swap", "sets", "reps", "effort", "remove", "volume"]


# ---------- What the AI returns (strict JSON schema: every field present, unused ones null) ----------

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProposedEdit(_Strict):
    kind: EditKind
    day: int | None                      # slot edits: the session's day
    slot_id: str | None                  # slot edits: the slot in that session
    new_exercise_id: ExerciseId | None   # swap
    muscle: Muscle | None                # volume
    sets_change: int | None              # sets
    reps: int | None                     # reps: bottom of the range
    reps_max: int | None                 # reps: top of the range
    target_rpe: float | None             # effort
    weekly_sets: int | None              # volume
    for_injury: bool
    reason: str


class ProgramReview(_Strict):
    summary: str
    edits: list[ProposedEdit]


# ---------- What the lifter sees ----------

class AppliedEdit(Model):
    kind: EditKind
    day: int | None
    slot_id: str | None
    muscle: str | None
    change: str              # written by the engine from the program, e.g. "Barbell Row: 5 → 4 sets"
    reason: str              # the AI's reason, shown labelled as AI-written
    for_injury: bool


class ReviewOutcome(Model):
    program: Program         # the edited program, or the baseline when there are violations
    summary: str
    applied: list[AppliedEdit]
    notes: list[str]         # guard notes (changes the guards made)
    violations: list[str]    # problems to send back to the AI; empty = accepted
    shortfalls: list[str]    # muscles the schedule can't bring to the starting volume


# ---------- Review ----------

def review_program(review: ProgramReview, profile: Profile, block: int = 1) -> ReviewOutcome:
    """Validate and apply `review` to the engine's baseline for `profile`."""
    first, shortfalls = build_week_one(profile)
    baseline_first = first.model_copy(deep=True)

    problems = _validate(review, first, profile)
    applied: list[AppliedEdit] = []
    overrides: dict[str, float] = {}
    if not problems:
        applied, overrides, problems = _apply(review, first, profile)
    if not problems:
        problems = _check_week_one(first, baseline_first, profile)

    if problems:
        program, notes, _ = expand_block(baseline_first, profile, block)
        return ReviewOutcome(program=program, summary=review.summary, applied=[], notes=notes,
                             violations=problems, shortfalls=shortfalls)

    program, notes, violations = expand_block(first, profile, block, start_overrides=overrides)
    if violations:
        program, notes, _ = expand_block(baseline_first, profile, block)
        applied = []
    return ReviewOutcome(program=program, summary=review.summary, applied=applied, notes=notes,
                         violations=violations, shortfalls=shortfalls)


def allowed_alternatives(slot_id: str, current: str, session: Session, available: set[str]) -> list[str]:
    """Exercises the template itself would accept in this slot (with the lifter's equipment)."""
    if slot_id in SLOTS:
        options = SLOTS[slot_id]
    elif slot_id.startswith("x_"):     # an accessory the engine added for a muscle
        options = [e for lst in ACCESSORIES.values() if current in lst for e in lst]
    else:
        options = []
    in_session = {ex.exercise_id for ex in session.exercises}
    return list(dict.fromkeys(e for e in options
                              if e != current and e not in in_session and get_exercise(e).equipment in available))


def _validate(review: ProgramReview, first: Week, profile: Profile) -> list[str]:
    problems: list[str] = []
    if not 1 <= len(review.summary.strip()) <= MAX_SUMMARY:
        problems.append(f"summary must be 1-{MAX_SUMMARY} characters.")
    if len(review.edits) > MAX_EDITS:
        problems.append(f"at most {MAX_EDITS} edits in total (got {len(review.edits)}).")

    injured = bool(profile.injuries.strip())
    available = set(profile.equipment) | {"bodyweight"}
    band = goal_rules(profile.goal).volume.start_band
    slots = {(s.day, ex.slot_id): (s, ex) for s in first.sessions for ex in s.exercises}
    seen_slots: set[tuple[int | None, str | None]] = set()
    seen_muscles: set[str] = set()
    exercise_edits = volume_edits = 0

    for i, e in enumerate(review.edits, start=1):
        where = f"Edit {i} ({e.kind}" + ("" if e.kind == "volume" else f" Day {e.day} {e.slot_id}") + ")"
        bad = lambda msg: problems.append(f"{where}: {msg}")
        if not 1 <= len(e.reason.strip()) <= MAX_REASON:
            bad(f"reason must be 1-{MAX_REASON} characters.")
        if e.for_injury and not injured:
            bad("for_injury edits need an injury in the lifter's notes, and there is none.")

        if e.kind == "volume":
            volume_edits += 1
            if e.muscle not in VOLUME_TARGET_MUSCLES:
                bad(f"{e.muscle} has no volume target; choose one of {', '.join(VOLUME_TARGET_MUSCLES)}.")
            elif e.muscle in seen_muscles:
                bad("only one volume edit per muscle.")
            seen_muscles.add(str(e.muscle))
            if e.weekly_sets is None or not band.low <= e.weekly_sets <= band.high:
                bad(f"weekly_sets must be within the {profile.goal} band {band.low:g}-{band.high:g}.")
            continue

        key = (e.day, e.slot_id)
        if key not in slots:
            bad(f"there is no slot {e.slot_id} on day {e.day}.")
            continue
        if key in seen_slots:
            bad("only one edit per slot.")
            continue
        seen_slots.add(key)
        if not e.for_injury:
            exercise_edits += 1
        session, ex = slots[key]
        info = get_exercise(ex.exercise_id)
        rules = rules_for_exercise(profile.goal, info)

        if e.kind == "swap":
            new = get_exercise(e.new_exercise_id) if e.new_exercise_id else None
            if new is None:
                bad("swap needs new_exercise_id.")
            elif new.id == ex.exercise_id:
                bad("that's already the exercise in this slot.")
            elif new.equipment not in available:
                bad(f"{new.id} needs {new.equipment} equipment, which the lifter doesn't have.")
            elif any(x.exercise_id == new.id for x in session.exercises):
                bad(f"{new.id} is already in this session.")
            elif e.for_injury:
                if not set(new.primary) & set(info.primary):
                    bad(f"an injury swap must train the same primary muscle ({', '.join(info.primary)}).")
            else:
                options = allowed_alternatives(ex.slot_id, ex.exercise_id, session, available)
                if new.id not in options:
                    bad(f"{new.id} isn't a listed alternative for this slot; allowed: {', '.join(options) or 'none'}"
                        " (set for_injury only if the swap is because of the lifter's injury).")
        elif e.kind == "sets":
            change = e.sets_change or 0
            if change == 0:
                bad("sets needs a non-zero sets_change.")
            elif change > 1 or (change < -1 and not e.for_injury):
                bad("sets_change must be ±1 (an injury may reduce more).")
            elif not 1 <= ex.sets + change <= MAX_SETS_PER_EXERCISE:
                bad(f"sets would become {ex.sets + change}; keep 1-{MAX_SETS_PER_EXERCISE}.")
        elif e.kind == "reps":
            lo, hi = int(rules.reps_limit.low), int(rules.reps_limit.high)
            if info.timed:
                bad("this is a timed exercise (seconds); reps can't be edited.")
            elif e.reps is None or e.reps_max is None or not lo <= e.reps <= e.reps_max <= hi:
                bad(f"rep range must be within {lo}-{hi} with reps <= reps_max.")
        elif e.kind == "effort":
            rng = target_rpe_range(rules)
            low = max(5.0, rng.low - (INJURY_RPE_DROP if e.for_injury else 0))
            rpe = e.target_rpe
            if rpe is None or not low <= rpe <= rng.high or (rpe * 2) % 1:
                bad(f"target RPE must be {low:g}-{rng.high:g} in steps of 0.5"
                    + ("" if e.for_injury else " (lower only for an injury)") + ".")
        elif e.kind == "remove":
            if not e.for_injury:
                bad("remove is only for injuries (set for_injury).")
            elif len(session.exercises) <= 1:
                bad("can't remove the only exercise in a session.")

    if exercise_edits > MAX_EXERCISE_EDITS:
        problems.append(f"at most {MAX_EXERCISE_EDITS} exercise edits unless they're for an injury "
                        f"(got {exercise_edits}); keep only the most important.")
    if volume_edits > MAX_VOLUME_EDITS:
        problems.append(f"at most {MAX_VOLUME_EDITS} volume edits (got {volume_edits}).")
    return problems


def _apply(review: ProgramReview, first: Week, profile: Profile
           ) -> tuple[list[AppliedEdit], dict[str, float], list[str]]:
    """Apply validated edits to week 1 in place. Returns (applied, volume overrides, problems)."""
    applied: list[AppliedEdit] = []
    frozen: set[tuple[int, str]] = set()
    overrides: dict[str, float] = {}
    baseline_sets = weekly_muscle_sets(first)

    for e in review.edits:
        if e.kind == "volume":
            overrides[str(e.muscle)] = float(e.weekly_sets)
            change = f"{e.muscle}: {baseline_sets.get(str(e.muscle), 0.0):g} → {e.weekly_sets:g} weekly sets in week 1"
        else:
            session = next(s for s in first.sessions if s.day == e.day)
            index, ex = next((i, x) for i, x in enumerate(session.exercises) if x.slot_id == e.slot_id)
            change = _apply_to_slot(e, session, index, ex, profile)
            frozen.add((session.day, ex.slot_id))
        applied.append(AppliedEdit(kind=e.kind, day=e.day if e.kind != "volume" else None,
                                   slot_id=e.slot_id if e.kind != "volume" else None,
                                   muscle=str(e.muscle) if e.kind == "volume" else None,
                                   change=change, reason=e.reason.strip(), for_injury=e.for_injury))

    # Keep every muscle at its target around the edits (edited slots stay as edited), then trim
    # muscles the coach lowered.
    volume = goal_rules(profile.goal).volume
    problems: list[str] = []
    fill_volume(first, profile, volume.start.low, overrides=overrides, frozen=frozen)
    after = weekly_muscle_sets(first)
    for muscle, target in overrides.items():
        if after.get(muscle, 0.0) < target:
            problems.append(f"{muscle}: can't fit {target:g} weekly sets in this schedule "
                            f"(got {after.get(muscle, 0.0):g}); choose a lower number.")
        elif after.get(muscle, 0.0) > target and not reduce_volume(first, muscle, target, frozen):
            problems.append(f"{muscle}: can't go as low as {target:g} weekly sets without dropping main lifts "
                            f"(lowest is {weekly_muscle_sets(first).get(muscle, 0.0):g}); choose a higher number.")
    return applied, overrides, problems


def _apply_to_slot(e: ProposedEdit, session: Session, index: int, ex, profile: Profile) -> str:
    info: ExerciseInfo = get_exercise(ex.exercise_id)
    if e.kind == "swap":
        new = get_exercise(e.new_exercise_id)
        session.exercises[index] = make_prescription(ex.slot_id, new, profile, ex.sets)
        return f"{info.name} → {new.name}"
    if e.kind == "sets":
        old, ex.sets = ex.sets, ex.sets + e.sets_change
        return f"{info.name}: {old} → {ex.sets} sets"
    if e.kind == "reps":
        old = f"{ex.reps}-{ex.reps_max or ex.reps}"
        ex.reps, ex.reps_max = e.reps, e.reps_max
        return f"{info.name}: {old} → {ex.reps}-{ex.reps_max} reps"
    if e.kind == "effort":
        old, ex.target_rpe = ex.target_rpe, e.target_rpe
        return f"{info.name}: RPE {old:g} → {ex.target_rpe:g}"
    session.exercises.remove(ex)        # remove
    return f"Removed {info.name}"


def _check_week_one(first: Week, baseline: Week, profile: Profile) -> list[str]:
    """Edited week 1 still fits each session's time and keeps changed muscles within the band."""
    problems: list[str] = []
    for session, before in zip(first.sessions, baseline.sessions):
        minutes, limit = _session_minutes(session.exercises), max(profile.session_minutes,
                                                                   _session_minutes(before.exercises))
        if minutes > limit:
            problems.append(f"Day {session.day} would take about {minutes:.0f} minutes, over the "
                            f"{profile.session_minutes}-minute limit; remove sets or keep it shorter.")
    band = goal_rules(profile.goal).volume.start_band
    now, then = weekly_muscle_sets(first), weekly_muscle_sets(baseline)
    outside = lambda v: max(band.low - v, v - band.high, 0.0)
    for muscle in VOLUME_TARGET_MUSCLES:
        new, old = now.get(muscle, 0.0), then.get(muscle, 0.0)
        if new != old and outside(new) > outside(old):
            problems.append(f"{muscle}: {new:g} weekly sets in week 1 is outside the {profile.goal} band "
                            f"{band.low:g}-{band.high:g}.")
    return problems
