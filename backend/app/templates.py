"""Rule-based starter programs (no LLM).

Builds a 4-week block + deload week from a Profile:
  - split by days per week (full body, upper/lower, PPL, ...)
  - each slot picks the first exercise the lifter has equipment for
  - rep ranges and target RPE come from rules.py for that goal and lift type
  - starting loads come from the lifter's lift estimates; unknown loads stay
    empty so the lifter picks a working weight on the first session
  - sessions are trimmed to fit the time budget, accessories first
  - the deload week cuts sets and effort per the goal's deload rules
Every week is passed through the guards, so templates can't break the rules.

Templates are also the fallback when the AI is unavailable.
"""

from app.exercises import ExerciseInfo, get_exercise
from app.guards import guard_week
from app.rules import ExerciseRules, goal_rules, rules_for_exercise
from app.schemas import PrescribedExercise, Profile, Program, Session, Week
from app.training import floor_to_increment

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
    # Unrounded Epley (epley_e1rm rounds for display, which can cost a whole load step here)
    e1rm = estimate.weight * (1 + estimate.reps / 30) if estimate.reps > 1 else estimate.weight
    rir = 10 - target_rpe
    load = e1rm / (1 + (reps + rir) / 30)
    return floor_to_increment(load, profile.units)   # round down: start conservative


def pick_exercise(slot: str, profile: Profile, used: set[str]) -> ExerciseInfo | None:
    available = set(profile.equipment) | {"bodyweight"}
    for exercise_id in SLOTS[slot]:
        info = get_exercise(exercise_id)
        if info and info.equipment in available and exercise_id not in used:
            return info
    return None


def _session_minutes(exercises: list[PrescribedExercise]) -> float:
    return sum(ex.sets * (ex.rest_sec + WORK_SECONDS_PER_SET) / 60 for ex in exercises)


def build_session(day: int, name: str, slots: list[str], profile: Profile) -> Session:
    sets = SETS_PER_EXERCISE[profile.days_per_week]
    used: set[str] = set()
    exercises: list[PrescribedExercise] = []

    for slot in slots:
        info = pick_exercise(slot, profile, used)
        if info is None:
            continue
        used.add(info.id)
        rules = rules_for_exercise(profile.goal, info)
        if info.timed:
            reps, reps_max = 30, 60
        else:
            reps, reps_max = template_rep_range(rules)
        rpe = template_target_rpe(rules)
        exercises.append(PrescribedExercise(
            slot_id=slot,
            exercise_id=info.id,
            sets=sets,
            reps=reps,
            reps_max=reps_max,
            load=starting_load(profile, info, reps, rpe),
            target_rpe=rpe,
            rest_sec=REST_SEC[info.kind],
        ))

    # Fit the time budget: drop from the end (isolation work goes first).
    while len(exercises) > MIN_EXERCISES_PER_SESSION and _session_minutes(exercises) > profile.session_minutes:
        exercises.pop()

    return Session(day=day, focus=name, exercises=exercises)


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


def build_template(profile: Profile, block: int = 1) -> tuple[Program, list[str]]:
    """Return the program plus any guard notes/violations (expected to be empty)."""
    split = SPLITS[profile.days_per_week]
    first = Week(week=1, sessions=[
        build_session(day, name, slots, profile) for day, (name, slots) in enumerate(split, start=1)
    ])

    weeks = [first] + [first.model_copy(update={"week": n}, deep=True) for n in range(2, BLOCK_WEEKS + 1)]
    weeks.append(deload_week(first, profile))

    issues: list[str] = []
    checked: list[Week] = []
    previous: Week | None = None
    for week in weeks:
        result = guard_week(week, profile.goal, profile.units, previous=previous)
        issues += result.notes + result.violations
        checked.append(result.week)
        previous = result.week

    return Program(block=block, units=profile.units, weeks=checked), issues
