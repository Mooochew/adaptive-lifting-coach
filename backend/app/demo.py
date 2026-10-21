"""Demo athlete: a realistic profile, program and 2.5 weeks of logs.

The program is the real 4-day template for a "general" (hybrid) lifter. Logged
reps and RPE are scripted, but every load change comes from the real engine:
after each finished week, `advance_program` writes the engine's suggestions
into the following weeks, exactly as the app does.

Shows off: rep progression before weight, a weight jump at the top of the range,
the overshoot rule on a small lift (lateral raise 20 lb builds to 18 reps before
jumping to 25), warm-up sets, and a session in progress.
"""

from app.progression import advance_program
from app.schemas import DemoAthlete, LiftEstimate, Profile, Program, SetLog
from app.templates import build_template

PROFILE = Profile(
    goal="general",
    experience="intermediate",
    days_per_week=4,
    session_minutes=75,
    equipment=["barbell", "dumbbell", "machine", "cable", "bodyweight"],
    units="lb",
    lift_estimates=[
        LiftEstimate(exercise_id="bench_press", weight=185, reps=6),
        LiftEstimate(exercise_id="back_squat", weight=275, reps=6),
        LiftEstimate(exercise_id="deadlift", weight=335, reps=6),
        LiftEstimate(exercise_id="overhead_press", weight=115, reps=6),
    ],
)

# Week-1 working weight per exercise.
START_LOADS = {
    "bench_press": 185, "barbell_row": 155, "lateral_raise": 20, "triceps_pushdown": 50,
    "back_squat": 275, "romanian_deadlift": 205, "leg_curl": 90, "calf_raise": 135,
    "overhead_press": 115, "lat_pulldown": 140, "incline_db_press": 60, "db_curl": 30,
    "deadlift": 335, "leg_press": 360, "bulgarian_split_squat": 40, "plank": 0, "face_pull": 40,
}

# exercise -> week -> (reps per set, RPE). Missing sets repeat the last value.
# Week 3 has only Day 1's bench, mid-session.
SCRIPT: dict[str, dict[int, tuple[list[int], float]]] = {
    "bench_press":           {1: ([4, 4, 3], 8), 2: ([5, 5, 4], 8), 3: ([5, 5], 8)},
    "barbell_row":           {1: ([8, 7, 7], 8), 2: ([9, 8, 8], 8)},
    "lateral_raise":         {1: ([16, 15, 15], 9), 2: ([18, 18, 18], 9)},   # overshoot before 20 -> 25
    "triceps_pushdown":      {1: ([12, 11, 10], 9), 2: ([13, 12, 12], 9)},
    "back_squat":            {1: ([5, 5, 5], 8), 2: ([5, 4, 4], 8)},
    "romanian_deadlift":     {1: ([8, 8, 8], 8), 2: ([8, 8, 7], 8.5)},
    "leg_curl":              {1: ([12, 12, 11], 9), 2: ([14, 13, 13], 9)},
    "calf_raise":            {1: ([15, 15, 15], 9), 2: ([12, 12, 11], 9)},
    "overhead_press":        {1: ([5, 5, 4], 8), 2: ([5, 5, 5], 8)},
    "lat_pulldown":          {1: ([10, 10, 9], 8), 2: ([10, 10, 10], 8)},
    "incline_db_press":      {1: ([10, 9, 9], 8), 2: ([10, 10, 10], 8)},
    "db_curl":               {1: ([12, 11, 10], 9), 2: ([12, 12, 11], 9)},
    "deadlift":              {1: ([5, 5, 5], 8), 2: ([4, 4, 4], 8)},
    "leg_press":             {1: ([10, 10, 10], 8), 2: ([9, 8, 8], 8)},
    "bulgarian_split_squat": {1: ([10, 10, 9], 8), 2: ([10, 10, 10], 8)},
    "plank":                 {1: ([45, 45, 40], 8), 2: ([50, 50, 45], 8)},
    "face_pull":             {1: ([13, 12, 12], 9), 2: ([14, 13, 13], 9)},
}

WARMUPS = {"bench_press": [(45, 10), (95, 5), (135, 3)],
           "back_squat": [(45, 10), (135, 5), (185, 3), (225, 2)],
           "deadlift": [(135, 5), (225, 3), (275, 2)]}

LOGGED_WEEKS = (1, 2, 3)


def _seed_loads(program: Program) -> None:
    for week in program.weeks:
        for session in week.sessions:
            for ex in session.exercises:
                if ex.exercise_id in START_LOADS:
                    ex.load = START_LOADS[ex.exercise_id]


def _reps_for(pattern: list[int], sets: int) -> list[int]:
    return [pattern[min(i, len(pattern) - 1)] for i in range(sets)]


def build_demo() -> DemoAthlete:
    program, _, _ = build_template(PROFILE)
    _seed_loads(program)

    logs: list[SetLog] = []
    for week_no in LOGGED_WEEKS:
        week = next(w for w in program.weeks if w.week == week_no)
        for session in week.sessions:
            for ex in session.exercises:
                script = SCRIPT.get(ex.exercise_id, {}).get(week_no)
                if script is None:
                    continue
                pattern, rpe = script
                reps = pattern if week_no == 3 else _reps_for(pattern, ex.sets)   # week 3: in progress
                common = dict(week=week_no, day=session.day, slot_id=ex.slot_id, exercise_id=ex.exercise_id)
                for i, (w, r) in enumerate(WARMUPS.get(ex.exercise_id, []), start=1):
                    logs.append(SetLog(**common, set_type="warmup", set_number=i, weight=w, reps=r))
                logs += [SetLog(**common, set_number=i, weight=ex.load or 0, reps=r, rpe=rpe)
                         for i, r in enumerate(reps, start=1)]
        program, _, _ = advance_program(PROFILE.goal, program, logs)

    return DemoAthlete(profile=PROFILE, program=program, logs=logs)
