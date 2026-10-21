"""Demo athlete: a realistic profile, program and 2.5 weeks of logs.

The program is the real 4-day template for a "general" (hybrid) lifter. Logged
reps and RPE are scripted, but every load change comes from the progression
engine itself: after each logged week, the slot's suggested load becomes the
next week's prescription. So the demo always agrees with the rules.

Shows off: rep progression before weight, weight jumps at the top of the range,
a lateral raise ready to go up, warm-up sets, and a session in progress.
"""

from app.exercises import get_exercise
from app.rules import goal_rules, rules_for_exercise
from app.schemas import DemoAthlete, LiftEstimate, Profile, Program, SetLog
from app.templates import BLOCK_WEEKS, build_template
from app.training import floor_to_increment, suggest_next_step

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

# Week-1 working weights per slot.
START_LOADS = {
    "bench": 185, "row": 155, "lateral": 20, "pushdown": 50,
    "squat": 275, "rdl": 205, "leg_curl": 90, "calf": 135,
    "ohp": 115, "pulldown": 140, "incline": 60, "curl": 30,
    "deadlift": 335, "legpress": 360, "split_squat": 40, "core": 0,
}

# slot -> week -> (reps per set, RPE). Week 3 has only Day 1's bench, mid-session.
SCRIPT: dict[str, dict[int, tuple[list[int], float]]] = {
    "bench":       {1: ([4, 4, 3], 8), 2: ([5, 5, 4], 8), 3: ([5, 5], 8)},
    "row":         {1: ([8, 7, 7], 8), 2: ([9, 8, 8], 8)},
    "lateral":     {1: ([13, 12, 12], 9), 2: ([15, 15, 15], 9)},
    "pushdown":    {1: ([12, 11, 10], 9), 2: ([13, 12, 12], 9)},
    "squat":       {1: ([5, 5, 5], 8), 2: ([5, 4, 4], 8)},
    "rdl":         {1: ([8, 8, 8], 8), 2: ([8, 8, 7], 8.5)},
    "leg_curl":    {1: ([12, 12, 11], 9), 2: ([14, 13, 13], 9)},
    "calf":        {1: ([15, 15, 15], 9), 2: ([12, 12, 11], 9)},
    "ohp":         {1: ([5, 5, 4], 8), 2: ([5, 5, 5], 8)},
    "pulldown":    {1: ([10, 10, 9], 8), 2: ([10, 10, 10], 8)},
    "incline":     {1: ([10, 9, 9], 8), 2: ([10, 10, 10], 8)},
    "curl":        {1: ([12, 11, 10], 9), 2: ([12, 12, 11], 9)},
    "deadlift":    {1: ([5, 5, 5], 8), 2: ([4, 4, 4], 8)},
    "legpress":    {1: ([10, 10, 10], 8), 2: ([9, 8, 8], 8)},
    "split_squat": {1: ([10, 10, 9], 8), 2: ([10, 10, 10], 8)},
    "core":        {1: ([45, 45, 40], 8), 2: ([50, 50, 45], 8)},
}

WARMUPS = {"bench": [(45, 10), (95, 5), (135, 3)],
           "squat": [(45, 10), (135, 5), (185, 3), (225, 2)],
           "deadlift": [(135, 5), (225, 3), (275, 2)]}


def _set_load(program: Program, day: int, slot: str, from_week: int, load: float) -> None:
    """Prescribe `load` for this slot from `from_week` through the deload week."""
    deload = goal_rules(PROFILE.goal).deload
    for week in program.weeks:
        if week.week < from_week:
            continue
        for session in week.sessions:
            if session.day != day:
                continue
            for ex in session.exercises:
                if ex.slot_id == slot:
                    ex.load = (floor_to_increment(load * (1 - deload.load_cut), program.units)
                               if week.is_deload and load else load)


def build_demo() -> DemoAthlete:
    program, _ = build_template(PROFILE)
    for session in program.weeks[0].sessions:
        for ex in session.exercises:
            if ex.slot_id in START_LOADS:
                _set_load(program, session.day, ex.slot_id, 1, START_LOADS[ex.slot_id])

    logs: list[SetLog] = []
    for week in program.weeks[:3]:
        for session in week.sessions:
            for ex in session.exercises:
                script = SCRIPT.get(ex.slot_id, {}).get(week.week)
                if script is None:
                    continue
                reps, rpe = script
                common = dict(week=week.week, day=session.day, slot_id=ex.slot_id, exercise_id=ex.exercise_id)

                for i, (w, r) in enumerate(WARMUPS.get(ex.slot_id, []), start=1):
                    logs.append(SetLog(**common, set_type="warmup", set_number=i, weight=w, reps=r))
                sets = [SetLog(**common, set_number=i, weight=ex.load or 0, reps=r, rpe=rpe)
                        for i, r in enumerate(reps, start=1)]
                logs += sets

                if len(sets) >= ex.sets and week.week < BLOCK_WEEKS:
                    info = get_exercise(ex.exercise_id)
                    _, _, load, _ = suggest_next_step(
                        rules_for_exercise(PROFILE.goal, info), info, ex, sets, program.units
                    )
                    _set_load(program, session.day, ex.slot_id, week.week + 1, load)

    return DemoAthlete(profile=PROFILE, program=program, logs=logs)
