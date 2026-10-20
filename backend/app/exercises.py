"""Canonical exercise library.

The LLM must pick exercises by id from this list, so the same lift is always
tracked under the same id (no "Bench" vs "Barbell Bench Press" drift).

Each exercise lists the muscles it trains. For weekly volume, a hard set
counts as 1 set for each primary muscle and 0.5 for each secondary muscle.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

Equipment = Literal["barbell", "dumbbell", "machine", "cable", "bodyweight", "kettlebell"]

# main:      competition-style barbell lifts (squat/bench/deadlift/OHP + close variants)
# compound:  other multi-joint movements
# isolation: single-joint movements
Kind = Literal["main", "compound", "isolation"]

Muscle = Literal[
    "chest", "front_delts", "side_delts", "rear_delts", "triceps", "biceps",
    "lats", "upper_back", "lower_back", "quads", "hamstrings", "glutes",
    "calves", "abs",
]

PRIMARY_SET_CREDIT = 1.0
SECONDARY_SET_CREDIT = 0.5


class ExerciseInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    equipment: Equipment
    kind: Kind
    primary: tuple[Muscle, ...]
    secondary: tuple[Muscle, ...] = ()
    timed: bool = False              # "reps" are seconds (e.g. plank); rep limits don't apply


def E(id: str, name: str, equipment: Equipment, kind: Kind,
      primary: tuple[Muscle, ...], secondary: tuple[Muscle, ...] = (), timed: bool = False) -> ExerciseInfo:
    return ExerciseInfo(id=id, name=name, equipment=equipment, kind=kind,
                        primary=primary, secondary=secondary, timed=timed)


EXERCISES: tuple[ExerciseInfo, ...] = (
    # Main lifts
    E("back_squat", "Barbell Back Squat", "barbell", "main", ("quads", "glutes"), ("lower_back",)),
    E("front_squat", "Barbell Front Squat", "barbell", "main", ("quads",), ("glutes", "upper_back")),
    E("deadlift", "Conventional Deadlift", "barbell", "main", ("glutes", "hamstrings", "lower_back"), ("quads", "upper_back")),
    E("bench_press", "Barbell Bench Press", "barbell", "main", ("chest",), ("triceps", "front_delts")),
    E("paused_bench", "Paused Bench Press", "barbell", "main", ("chest",), ("triceps", "front_delts")),
    E("overhead_press", "Standing Overhead Press", "barbell", "main", ("front_delts",), ("triceps", "side_delts")),
    # Compounds
    E("romanian_deadlift", "Romanian Deadlift", "barbell", "compound", ("hamstrings", "glutes"), ("lower_back",)),
    E("close_grip_bench", "Close-Grip Bench Press", "barbell", "compound", ("triceps", "chest"), ("front_delts",)),
    E("barbell_row", "Barbell Bent-Over Row", "barbell", "compound", ("upper_back", "lats"), ("biceps", "rear_delts", "lower_back")),
    E("incline_db_press", "Incline Dumbbell Press", "dumbbell", "compound", ("chest",), ("front_delts", "triceps")),
    E("db_bench_press", "Dumbbell Bench Press", "dumbbell", "compound", ("chest",), ("triceps", "front_delts")),
    E("db_shoulder_press", "Seated Dumbbell Shoulder Press", "dumbbell", "compound", ("front_delts",), ("triceps", "side_delts")),
    E("db_row", "One-Arm Dumbbell Row", "dumbbell", "compound", ("lats", "upper_back"), ("biceps", "rear_delts")),
    E("goblet_squat", "Goblet Squat", "dumbbell", "compound", ("quads",), ("glutes",)),
    E("bulgarian_split_squat", "Bulgarian Split Squat", "dumbbell", "compound", ("quads", "glutes")),
    E("leg_press", "Leg Press", "machine", "compound", ("quads",), ("glutes",)),
    E("lat_pulldown", "Lat Pulldown", "cable", "compound", ("lats",), ("biceps", "upper_back")),
    E("seated_cable_row", "Seated Cable Row", "cable", "compound", ("upper_back", "lats"), ("biceps", "rear_delts")),
    E("pull_up", "Pull-Up", "bodyweight", "compound", ("lats",), ("biceps", "upper_back")),
    E("dip", "Parallel Bar Dip", "bodyweight", "compound", ("chest", "triceps"), ("front_delts",)),
    E("push_up", "Push-Up", "bodyweight", "compound", ("chest",), ("triceps", "front_delts")),
    E("kb_swing", "Kettlebell Swing", "kettlebell", "compound", ("glutes", "hamstrings"), ("lower_back",)),
    # Isolation
    E("lateral_raise", "Dumbbell Lateral Raise", "dumbbell", "isolation", ("side_delts",)),
    E("face_pull", "Cable Face Pull", "cable", "isolation", ("rear_delts",), ("upper_back",)),
    E("triceps_pushdown", "Triceps Rope Pushdown", "cable", "isolation", ("triceps",)),
    E("db_curl", "Dumbbell Biceps Curl", "dumbbell", "isolation", ("biceps",)),
    E("leg_curl", "Lying Leg Curl", "machine", "isolation", ("hamstrings",)),
    E("leg_extension", "Leg Extension", "machine", "isolation", ("quads",)),
    E("calf_raise", "Standing Calf Raise", "machine", "isolation", ("calves",)),
    E("plank", "Plank", "bodyweight", "isolation", ("abs",), timed=True),
)

EXERCISES_BY_ID: dict[str, ExerciseInfo] = {e.id: e for e in EXERCISES}


def is_known(exercise_id: str) -> bool:
    return exercise_id in EXERCISES_BY_ID


def get_exercise(exercise_id: str) -> ExerciseInfo | None:
    return EXERCISES_BY_ID.get(exercise_id)


def exercise_name(exercise_id: str) -> str:
    info = EXERCISES_BY_ID.get(exercise_id)
    return info.name if info else exercise_id


def muscle_credits(exercise_id: str) -> dict[str, float]:
    """Sets credited to each muscle for one hard set of this exercise."""
    info = EXERCISES_BY_ID.get(exercise_id)
    if info is None:
        return {}
    credits = {m: SECONDARY_SET_CREDIT for m in info.secondary}
    credits.update({m: PRIMARY_SET_CREDIT for m in info.primary})
    return credits
