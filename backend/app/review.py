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

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

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

def baseline_outcome(profile: Profile, block: int = 1) -> ReviewOutcome:
    """The engine's program with no edits (what the lifter gets when the AI can't help)."""
    first, shortfalls = build_week_one(profile)
    program, notes, violations = expand_block(first, profile, block)
    return ReviewOutcome(program=program, summary="", applied=[], notes=notes, violations=violations,
                         shortfalls=shortfalls)


def review_program(review: ProgramReview, profile: Profile, block: int = 1) -> ReviewOutcome:
    """Validate and apply `review` to the engine's baseline for `profile`."""
    first, shortfalls = build_week_one(profile)
    baseline_first = first.model_copy(deep=True)

    problems = _validate(review, first, profile)
    applied: list[AppliedEdit] = []
    overrides: dict[str, float] = {}
    edited_shortfalls = shortfalls
    excluded: dict[int, set[str]] = {}
    if not problems:
        applied, overrides, problems, edited_shortfalls, excluded = _apply(review, first, profile)
    if not problems:
        problems = _check_week_one(first, baseline_first, profile, _injury_muscles(review, baseline_first))

    if problems:
        program, notes, _ = expand_block(baseline_first, profile, block)
        return ReviewOutcome(program=program, summary=review.summary, applied=[], notes=notes,
                             violations=problems, shortfalls=shortfalls)

    program, notes, violations = expand_block(first, profile, block, start_overrides=overrides, excluded=excluded)
    # What the lifter is told was taken out must stay out, in every week.
    violations += [f"Week {w.week} Day {s.day} still has {e.exercise_id} after it was taken out."
                   for w in program.weeks for s in w.sessions for e in s.exercises
                   if e.exercise_id in excluded.get(s.day, set())]
    if not violations:
        # Edits mutate models in place, which skips their validators: re-check the whole program
        # so the lifter can never receive one the API itself would reject (e.g. an empty session).
        try:
            Program.model_validate(program.model_dump())
        except ValidationError as err:
            violations = [f"the edited program isn't valid: {e['msg']}" for e in err.errors()]
    if violations:
        program, notes, _ = expand_block(baseline_first, profile, block)
        applied, edited_shortfalls = [], shortfalls
    return ReviewOutcome(program=program, summary=review.summary, applied=applied, notes=notes,
                         violations=violations, shortfalls=edited_shortfalls)


# Injury text that means "no injury" (so it can't unlock the injury-only edits).
NO_INJURY = {"", "none", "no", "nope", "na", "n/a", "nil", "nothing", "no injuries", "no injury", "none so far",
             "not applicable"}


def has_injury(text: str) -> bool:
    return re.sub(r"[^a-z/ ]", "", text.lower()).strip() not in NO_INJURY


def allowed_alternatives(slot_id: str, current: str, session: Session, available: set[str]) -> list[str]:
    """Exercises the template itself would accept in this slot (with the lifter's equipment)."""
    if slot_id in SLOTS:
        options = SLOTS[slot_id]
    elif slot_id.startswith("x_"):     # an accessory the engine added: others for its own primary muscles
        options = [e for m in get_exercise(current).primary for e in ACCESSORIES.get(m, []) if current in ACCESSORIES[m]]
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

    injured = has_injury(profile.injuries)
    removals: dict[int, int] = {}
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
            # Same range for injury edits: the guards hold every week to it, and the card must
            # only promise what the plan prescribes.
            rng = target_rpe_range(rules)
            rpe = e.target_rpe
            if rpe is None or not rng.low <= rpe <= rng.high or (rpe * 2) % 1:
                bad(f"target RPE must be {rng.low:g}-{rng.high:g} in steps of 0.5.")
        elif e.kind == "remove":
            removals[session.day] = removals.get(session.day, 0) + 1
            if not e.for_injury:
                bad("remove is only for injuries (set for_injury).")
            elif len(session.exercises) - removals[session.day] < 1:
                bad(f"that would leave Day {session.day} with no exercises; keep at least one.")

    if exercise_edits > MAX_EXERCISE_EDITS:
        problems.append(f"at most {MAX_EXERCISE_EDITS} exercise edits unless they're for an injury "
                        f"(got {exercise_edits}); keep only the most important.")
    if volume_edits > MAX_VOLUME_EDITS:
        problems.append(f"at most {MAX_VOLUME_EDITS} volume edits (got {volume_edits}).")
    return problems


def _apply(review: ProgramReview, first: Week, profile: Profile
           ) -> tuple[list[AppliedEdit], dict[str, float], list[str], list[str], dict[int, set[str]]]:
    """Apply validated edits to week 1 in place.

    Returns (applied, volume overrides, problems, shortfalls of the edited week 1, and the
    exercises taken out of each day by swaps and removals, which must not come back)."""
    applied: list[AppliedEdit] = []
    frozen: set[tuple[int, str]] = set()
    overrides: dict[str, float] = {}
    excluded: dict[int, set[str]] = {}
    baseline_sets = weekly_muscle_sets(first)

    for e in review.edits:
        if e.kind == "volume":
            overrides[str(e.muscle)] = float(e.weekly_sets)
            change = ""                      # filled in below with the volume actually reached
        else:
            session = next(s for s in first.sessions if s.day == e.day)
            index, ex = next((i, x) for i, x in enumerate(session.exercises) if x.slot_id == e.slot_id)
            change, slot_id = _apply_to_slot(e, session, index, ex, profile)
            frozen.add((session.day, slot_id))
            if e.kind in ("swap", "remove"):
                excluded.setdefault(session.day, set()).add(ex.exercise_id)
        applied.append(AppliedEdit(kind=e.kind, day=e.day if e.kind != "volume" else None,
                                   slot_id=e.slot_id if e.kind != "volume" else None,
                                   muscle=str(e.muscle) if e.kind == "volume" else None,
                                   change=change, reason=e.reason.strip(), for_injury=e.for_injury))

    # Keep every muscle at its target around the edits (edited slots stay as edited) and trim the
    # muscles the coach lowered. Trimming also takes secondary work from other muscles, so refill
    # and trim again until it settles.
    volume = goal_rules(profile.goal).volume
    problems: list[str] = []
    stuck: dict[str, float] = {}
    shortfalls = fill_volume(first, profile, volume.start.low, overrides=overrides, frozen=frozen,
                                 excluded=excluded)
    for _ in range(4):
        stuck = {m: t for m, t in overrides.items()
                 if weekly_muscle_sets(first).get(m, 0.0) > t and not reduce_volume(first, m, t, frozen)}
        shortfalls = fill_volume(first, profile, volume.start.low, overrides=overrides, frozen=frozen,
                                 excluded=excluded)
        if all(weekly_muscle_sets(first).get(m, 0.0) <= t for m, t in overrides.items()):
            break
    after = weekly_muscle_sets(first)
    for muscle, target in overrides.items():
        if after.get(muscle, 0.0) < target:
            problems.append(f"{muscle}: can't fit {target:g} weekly sets in this schedule "
                            f"(got {after.get(muscle, 0.0):g}); choose a lower number.")
        elif muscle in stuck or after.get(muscle, 0.0) > target:
            problems.append(f"{muscle}: can't go as low as {target:g} weekly sets without dropping main lifts "
                            f"(lowest is {after.get(muscle, 0.0):g}); choose a higher number.")
    for a in applied:
        if a.kind == "volume":
            a.change = (f"{a.muscle}: {baseline_sets.get(a.muscle, 0.0):g} → {after.get(a.muscle, 0.0):g} "
                        "weekly sets in week 1")
    return applied, overrides, problems, shortfalls, excluded


def _apply_to_slot(e: ProposedEdit, session: Session, index: int, ex, profile: Profile) -> tuple[str, str]:
    """Apply one slot edit. Returns (the change as the lifter sees it, the slot's id afterwards)."""
    info: ExerciseInfo = get_exercise(ex.exercise_id)
    if e.kind == "swap":
        new = get_exercise(e.new_exercise_id)
        # An accessory the engine added is named after its exercise (x_dip); rename it so the
        # engine doesn't add the old exercise back under the same id.
        slot_id = f"x_{new.id}" if ex.slot_id.startswith("x_") else ex.slot_id
        if any(x.slot_id == slot_id for x in session.exercises if x is not ex):
            slot_id = ex.slot_id
        session.exercises[index] = make_prescription(slot_id, new, profile, ex.sets)
        return f"{info.name} → {new.name}", slot_id
    if e.kind == "sets":
        old, ex.sets = ex.sets, ex.sets + e.sets_change
        return f"{info.name}: {old} → {ex.sets} sets", ex.slot_id
    if e.kind == "reps":
        old = f"{ex.reps}-{ex.reps_max or ex.reps}"
        ex.reps, ex.reps_max = e.reps, e.reps_max
        return f"{info.name}: {old} → {ex.reps}-{ex.reps_max} reps", ex.slot_id
    if e.kind == "effort":
        old, ex.target_rpe = ex.target_rpe, e.target_rpe
        return f"{info.name}: RPE {old:g} → {ex.target_rpe:g}", ex.slot_id
    session.exercises.remove(ex)        # remove
    return f"Removed {info.name}", ex.slot_id


def _injury_muscles(review: ProgramReview, baseline: Week) -> set[str]:
    """Primary muscles of the exercises that injury edits changed: these may drop below the band
    (less shoulder work for a sore shoulder is the point), never above it."""
    slots = {(s.day, e.slot_id): e.exercise_id for s in baseline.sessions for e in s.exercises}
    return {m for e in review.edits if e.for_injury and (e.day, e.slot_id) in slots
            for m in get_exercise(slots[(e.day, e.slot_id)]).primary}


def _check_week_one(first: Week, baseline: Week, profile: Profile,
                    injury_muscles: set[str] = frozenset()) -> list[str]:
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
    outside = lambda v, m: max(0.0 if m in injury_muscles else band.low - v, v - band.high, 0.0)
    for muscle in VOLUME_TARGET_MUSCLES:
        new, old = now.get(muscle, 0.0), then.get(muscle, 0.0)
        if new != old and outside(new, muscle) > outside(old, muscle):
            problems.append(f"{muscle}: {new:g} weekly sets in week 1 is outside the {profile.goal} band "
                            f"{band.low:g}-{band.high:g}.")
    return problems
