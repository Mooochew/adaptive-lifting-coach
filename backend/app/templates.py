"""Rule-based starter programs (no LLM).

Builds a 4-week block + deload week from a Profile:
  - split by days per week (full body, upper/lower, PPL, ...)
  - each slot picks the first exercise the lifter has equipment for
  - rep ranges and target RPE come from rules.py for that goal and lift type
  - starting loads come from the lifter's lift estimates; unknown loads stay
    empty so the lifter picks a working weight on the first session
  - sessions are trimmed to fit the time budget, accessories first
  - sets are then added (or a fitting accessory) until every major muscle reaches the
    goal's starting weekly volume, and later weeks ramp by the goal's minimum ramp;
    muscles the schedule can't fit are reported as shortfalls, never hidden
  - the deload week cuts sets and effort per the goal's deload rules
Every week is passed through the guards, so templates can't break the rules.

Templates are also the fallback when the AI is unavailable.
"""

from app.exercises import ExerciseInfo, get_exercise, muscle_credits
from app.guards import guard_program, session_muscle_sets, weekly_muscle_sets
from app.rules import (
    MAX_SESSION_SETS_PER_MUSCLE,
    VOLUME_TARGET_MUSCLES,
    ExerciseRules,
    goal_rules,
    rules_for_exercise,
)
from app.schemas import PrescribedExercise, Profile, Program, Session, Week
from app.training import floor_to_increment, load_from_recent_set

class TemplateError(ValueError):
    """The profile can't be turned into a program (e.g. not enough equipment for a day)."""


BLOCK_WEEKS = 4                      # hard weeks, followed by one deload week
WORK_SECONDS_PER_SET = 45            # average time under the bar per set
MIN_EXERCISES_PER_SESSION = 3

REST_SEC = {"main": 180, "compound": 120, "isolation": 75}

# Slot -> exercise candidates in order of preference (first one the lifter can do wins).
SLOTS: dict[str, list[str]] = {
    "bench": ["bench_press", "db_bench_press", "push_up"],
    "incline": ["incline_db_press", "db_bench_press", "push_up"],
    "ohp": ["overhead_press", "db_shoulder_press"],
    "row": ["barbell_row", "seated_cable_row", "db_row"],
    "row2": ["seated_cable_row", "db_row", "barbell_row"],
    "pulldown": ["lat_pulldown", "pull_up"],
    "squat": ["back_squat", "goblet_squat", "leg_press", "bulgarian_split_squat"],
    "deadlift": ["deadlift", "romanian_deadlift", "kb_swing"],
    "rdl": ["romanian_deadlift", "kb_swing"],
    "legpress": ["leg_press", "goblet_squat", "bulgarian_split_squat"],
    "split_squat": ["bulgarian_split_squat", "goblet_squat"],
    "leg_curl": ["leg_curl"],
    "leg_ext": ["leg_extension"],
    "calf": ["calf_raise"],
    "lateral": ["lateral_raise"],
    "face_pull": ["face_pull"],
    "pushdown": ["triceps_pushdown", "dip", "close_grip_bench"],
    "curl": ["db_curl"],
    "core": ["plank"],
}

# Split per days/week: (session name, ordered slots). Main lifts first, isolation last.
SPLITS: dict[int, list[tuple[str, list[str]]]] = {
    2: [
        ("Full Body A", ["squat", "bench", "row", "leg_curl", "lateral", "curl"]),
        ("Full Body B", ["deadlift", "ohp", "pulldown", "legpress", "pushdown", "calf"]),
    ],
    3: [
        ("Full Body A", ["squat", "bench", "row", "leg_curl", "lateral"]),
        ("Full Body B", ["deadlift", "ohp", "pulldown", "split_squat", "curl"]),
        ("Full Body C", ["legpress", "incline", "row2", "calf", "pushdown"]),
    ],
    4: [
        ("Upper A", ["bench", "row", "lateral", "pushdown"]),
        ("Lower A", ["squat", "rdl", "leg_curl", "calf"]),
        ("Upper B", ["ohp", "pulldown", "incline", "curl"]),
        ("Lower B", ["deadlift", "legpress", "split_squat", "core"]),
    ],
    5: [
        ("Upper A", ["bench", "row", "lateral", "pushdown"]),
        ("Lower A", ["squat", "rdl", "leg_curl", "calf"]),
        ("Upper B", ["ohp", "pulldown", "incline", "curl"]),
        ("Lower B", ["deadlift", "legpress", "split_squat", "core"]),
        ("Arms & Delts", ["lateral", "face_pull", "curl", "pushdown"]),
    ],
    6: [
        ("Push A", ["bench", "ohp", "lateral", "pushdown"]),
        ("Pull A", ["row", "pulldown", "face_pull", "curl"]),
        ("Legs A", ["squat", "rdl", "leg_ext", "calf"]),
        ("Push B", ["ohp", "incline", "lateral", "pushdown"]),
        ("Pull B", ["deadlift", "row2", "face_pull", "curl"]),
        ("Legs B", ["legpress", "split_squat", "leg_curl", "core"]),
    ],
}

SETS_PER_EXERCISE = {2: 3, 3: 3, 4: 3, 5: 3, 6: 2}


def template_rep_range(rules: ExerciseRules) -> tuple[int, int]:
    """Bottom and top of the prescribed range for a starter program.

    Strength main lifts sit near the top of their 1-6 range (3-5);
    everything else starts at the bottom of its range (6-10, 10-15).
    """
    if rules.style == "strength":
        top = int(rules.reps.high) - 1
        return top - rules.rep_window, top
    bottom = int(rules.reps.low)
    return bottom, min(bottom + rules.rep_window, int(rules.reps.high))


def template_target_rpe(rules: ExerciseRules) -> float:
    """Middle of the target RIR range, as RPE (RIR 1-3 -> RPE 8, RIR 0-2 -> RPE 9)."""
    return 10 - (rules.rir.low + rules.rir.high) / 2


def starting_load(profile: Profile, info: ExerciseInfo, reps: int, target_rpe: float) -> float | None:
    """Working load for `reps` at `target_rpe`, from the lifter's own estimate of this lift."""
    if info.equipment == "bodyweight":
        return 0.0
    estimate = next((e for e in profile.lift_estimates if e.exercise_id == info.id), None)
    if estimate is None:
        return None
    return load_from_recent_set(estimate.weight, estimate.reps, reps, target_rpe, profile.units)


def pick_exercise(slot: str, profile: Profile, used: set[str]) -> ExerciseInfo | None:
    available = set(profile.equipment) | {"bodyweight"}
    for exercise_id in SLOTS[slot]:
        info = get_exercise(exercise_id)
        if info and info.equipment in available and exercise_id not in used:
            return info
    return None


def _session_minutes(exercises: list[PrescribedExercise]) -> float:
    return sum(ex.sets * (ex.rest_sec + WORK_SECONDS_PER_SET) / 60 for ex in exercises)


def make_prescription(slot_id: str, info: ExerciseInfo, profile: Profile, sets: int) -> PrescribedExercise:
    rules = rules_for_exercise(profile.goal, info)
    reps, reps_max = (30, 60) if info.timed else template_rep_range(rules)
    rpe = template_target_rpe(rules)
    return PrescribedExercise(
        slot_id=slot_id,
        exercise_id=info.id,
        sets=sets,
        reps=reps,
        reps_max=reps_max,
        load=starting_load(profile, info, reps, rpe),
        target_rpe=rpe,
        rest_sec=REST_SEC[info.kind],
    )


def build_session(day: int, name: str, slots: list[str], profile: Profile) -> Session:
    sets = SETS_PER_EXERCISE[profile.days_per_week]
    used: set[str] = set()
    exercises: list[PrescribedExercise] = []

    for slot in slots:
        info = pick_exercise(slot, profile, used)
        if info is None:
            continue
        used.add(info.id)
        exercises.append(make_prescription(slot, info, profile, sets))

    if not exercises:
        raise TemplateError(f"Not enough equipment to build {name}: the exercise library has nothing for "
                            f"that day with {', '.join(sorted(set(profile.equipment)))}. Add some equipment.")

    # Fit the time budget: drop from the end (isolation work goes first).
    while len(exercises) > MIN_EXERCISES_PER_SESSION and _session_minutes(exercises) > profile.session_minutes:
        exercises.pop()

    return Session(day=day, focus=name, exercises=exercises)


# ---------- Volume sizing ----------

MAX_SETS_PER_EXERCISE = 5
EXTRA_EXERCISE_SETS = 2
KIND_ORDER = {"isolation": 0, "compound": 1, "main": 2}   # grow accessories before main lifts

# Accessories that can be added to a session to reach a muscle's volume, best first.
ACCESSORIES: dict[str, list[str]] = {
    "chest": ["incline_db_press", "db_bench_press", "dip", "push_up"],
    "lats": ["lat_pulldown", "pull_up", "db_row"],
    "upper_back": ["seated_cable_row", "db_row", "face_pull"],
    "front_delts": ["db_shoulder_press", "overhead_press"],
    "side_delts": ["lateral_raise"],
    "rear_delts": ["face_pull"],
    "triceps": ["triceps_pushdown", "dip", "close_grip_bench"],
    "biceps": ["db_curl"],
    "quads": ["leg_extension", "leg_press", "goblet_squat", "bulgarian_split_squat"],
    "hamstrings": ["leg_curl", "romanian_deadlift"],
    "glutes": ["bulgarian_split_squat", "romanian_deadlift", "kb_swing"],
}


def _fits(week: Week, session: Session, extra_minutes: float, minutes: int) -> bool:
    return _session_minutes(session.exercises) + extra_minutes <= minutes


def _within_caps(week: Week, session: Session, change: dict[str, float], volume_max: float,
                 previous: dict[str, float] | None, ramp_max: float) -> bool:
    """Adding `change` (muscle -> sets) to `session` keeps every muscle under the weekly max,
    the weekly ramp limit, and the per-session ceiling."""
    weekly = weekly_muscle_sets(week)
    in_session = session_muscle_sets(session)
    for muscle, add in change.items():
        after = weekly.get(muscle, 0.0) + add
        if after > volume_max:
            return False
        if previous is not None and after - previous.get(muscle, 0.0) > ramp_max:
            return False
        if in_session.get(muscle, 0.0) + add > MAX_SESSION_SETS_PER_MUSCLE:
            return False
    return True


def _add_set_for(muscle: str, week: Week, profile: Profile, previous: dict[str, float] | None,
                 frozen: set[tuple[int, str]] = frozenset()) -> bool:
    """Add one set to an exercise training `muscle` directly, or add a fitting accessory.

    Spreads volume: prefers the session where this muscle has done the least so far.
    Slots in `frozen` ((day, slot_id), e.g. ones a coach just edited) are left as they are.
    """
    volume = goal_rules(profile.goal).volume
    muscle_load = lambda s: session_muscle_sets(s).get(muscle, 0.0)

    candidates = []
    for session in week.sessions:
        for ex in session.exercises:
            info = get_exercise(ex.exercise_id)
            if (session.day, ex.slot_id) in frozen:
                continue
            if muscle in info.primary and ex.sets < MAX_SETS_PER_EXERCISE:
                candidates.append(((muscle_load(session), KIND_ORDER[info.kind], ex.sets), session, ex, info))
    for _, session, ex, info in sorted(candidates, key=lambda c: c[0]):
        one_set = (ex.rest_sec + WORK_SECONDS_PER_SET) / 60
        if (_fits(week, session, one_set, profile.session_minutes)
                and _within_caps(week, session, muscle_credits(info.id), volume.max, previous, volume.ramp.high)):
            ex.sets += 1
            return True

    available = set(profile.equipment) | {"bodyweight"}
    # Prefer sessions that already train this muscle (presses on upper days) but have done the
    # least of it so far, then the least busy one.
    trains = lambda s: any(muscle in muscle_credits(ex.exercise_id) for ex in s.exercises)
    by_free_time = sorted(week.sessions,
                          key=lambda s: (not trains(s), muscle_load(s), _session_minutes(s.exercises)))
    for exercise_id in ACCESSORIES.get(muscle, []):
        info = get_exercise(exercise_id)
        if info.equipment not in available:
            continue
        for session in by_free_time:
            if any(ex.exercise_id == exercise_id for ex in session.exercises):
                continue
            new = make_prescription(f"x_{exercise_id}", info, profile, EXTRA_EXERCISE_SETS)
            minutes = new.sets * (new.rest_sec + WORK_SECONDS_PER_SET) / 60
            change = {m: c * new.sets for m, c in muscle_credits(exercise_id).items()}
            if (_fits(week, session, minutes, profile.session_minutes)
                    and _within_caps(week, session, change, volume.max, previous, volume.ramp.high)):
                session.exercises.append(new)
                return True
    return False


def fill_volume(week: Week, profile: Profile, target: float, previous: Week | None = None,
                overrides: dict[str, float] | None = None,
                frozen: set[tuple[int, str]] = frozenset()) -> list[str]:
    """Raise every targeted muscle to `target` weekly sets (or its own target in `overrides`)
    where the schedule allows, without touching `frozen` slots.

    Returns shortfalls for muscles that can't get there (too few days/minutes/equipment).
    """
    overrides = overrides or {}
    target_of = lambda m: overrides.get(m, target)
    previous_sets = weekly_muscle_sets(previous) if previous else None
    stuck: set[str] = set()
    for _ in range(500):
        current = weekly_muscle_sets(week)
        short = [m for m in VOLUME_TARGET_MUSCLES if current.get(m, 0.0) < target_of(m) and m not in stuck]
        if not short:
            break
        muscle = max(short, key=lambda m: target_of(m) - current.get(m, 0.0))
        if not _add_set_for(muscle, week, profile, previous_sets, frozen):
            stuck.add(muscle)
    current = weekly_muscle_sets(week)
    return [f"{m}: {current.get(m, 0.0):g} of {target_of(m):g} weekly sets (not enough time or equipment)"
            for m in sorted(stuck)]


def reduce_volume(week: Week, muscle: str, target: float,
                  frozen: set[tuple[int, str]] = frozenset()) -> bool:
    """Take sets away until `muscle` is at or below `target` weekly sets. Returns False if it
    can't get there (what's left comes from exercises that can't go lower, or secondary work).

    Order: added accessories first, then isolation, compound and main lifts, biggest first.
    Main lifts keep at least 2 sets, everything else at least 1; an added accessory may go.
    """
    for _ in range(200):
        if weekly_muscle_sets(week).get(muscle, 0.0) <= target:
            return True
        candidates = []
        for session in week.sessions:
            for ex in session.exercises:
                info = get_exercise(ex.exercise_id)
                added = ex.slot_id.startswith("x_")
                floor = 0 if added else (2 if info.kind == "main" else 1)
                if (session.day, ex.slot_id) in frozen or muscle not in info.primary or ex.sets <= floor:
                    continue
                candidates.append(((not added, KIND_ORDER[info.kind], -ex.sets), session, ex))
        if not candidates:
            return False
        _, session, ex = min(candidates, key=lambda c: c[0])
        ex.sets -= 1
        if ex.sets == 0:
            session.exercises.remove(ex)
    return weekly_muscle_sets(week).get(muscle, 0.0) <= target


def deload_week(week: Week, profile: Profile) -> Week:
    """Same sessions with fewer sets, easier targets and (for strength) lighter loads."""
    deload = goal_rules(profile.goal).deload
    sessions = []
    for s in week.sessions:
        exercises = []
        for ex in s.exercises:
            load = ex.load
            if load and deload.load_cut:
                load = floor_to_increment(load * (1 - deload.load_cut), profile.units)
            exercises.append(ex.model_copy(update={
                "sets": max(1, round(ex.sets * (1 - deload.set_cut))),
                "target_rpe": max(5.0, ex.target_rpe - deload.rir_add),
                "load": load,
            }))
        sessions.append(s.model_copy(update={"exercises": exercises}))
    return Week(week=BLOCK_WEEKS + 1, is_deload=True, sessions=sessions)


def build_template(profile: Profile, block: int = 1) -> tuple[Program, list[str], list[str]]:
    """Return (program, guard notes/violations, volume shortfalls).

    Week 1 starts every targeted muscle at the goal's starting volume; each later
    hard week ramps the target by the goal's minimum ramp (hypertrophy +1 set/week,
    strength flat). The deload week is built from week 1.
    """
    first, shortfalls = build_week_one(profile)
    program, notes, violations = expand_block(first, profile, block)
    return program, notes + violations, shortfalls


def build_week_one(profile: Profile) -> tuple[Week, list[str]]:
    """Week 1 with every targeted muscle at the goal's starting volume. Returns (week, shortfalls)."""
    split = SPLITS[profile.days_per_week]
    first = Week(week=1, sessions=[
        build_session(day, name, slots, profile) for day, (name, slots) in enumerate(split, start=1)
    ])
    return first, fill_volume(first, profile, goal_rules(profile.goal).volume.start.low)


def expand_block(first: Week, profile: Profile, block: int = 1,
                 start_overrides: dict[str, float] | None = None) -> tuple[Program, list[str], list[str]]:
    """Weeks 2-4 ramp from `first` by the goal's minimum ramp, then a deload built from `first`;
    everything goes through the guards. Returns (program, guard notes, guard violations).

    `start_overrides` (muscle -> week-1 weekly sets) moves a muscle's starting point, e.g. a
    coach's volume adjustment; that muscle then ramps from there like the others.
    """
    volume = goal_rules(profile.goal).volume
    weeks = [first]
    for n in range(2, BLOCK_WEEKS + 1):
        week = weeks[-1].model_copy(update={"week": n}, deep=True)
        ramp = volume.ramp.low * (n - 1)
        target = min(volume.start.low + ramp, volume.max)
        overrides = {m: min(sets + ramp, volume.max) for m, sets in (start_overrides or {}).items()}
        fill_volume(week, profile, target, previous=weeks[-1], overrides=overrides)
        weeks.append(week)
    weeks.append(deload_week(first, profile))

    result = guard_program(Program(block=block, units=profile.units, weeks=weeks), profile.goal,
                           estimates=profile.lift_estimates)
    return result.program, result.notes, result.violations
