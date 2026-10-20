import pytest

from app.schemas import PrescribedExercise, Program, Session, SetLog, Week


def bench_rx(**kw) -> PrescribedExercise:
    return PrescribedExercise(**{"slot_id": "bench", "exercise_id": "bench_press", "sets": 3,
                                 "reps": 5, "load": 185, "target_rpe": 8, **kw})


def lateral_rx(**kw) -> PrescribedExercise:
    return PrescribedExercise(**{"slot_id": "lateral", "exercise_id": "lateral_raise", "sets": 3,
                                 "reps": 12, "reps_max": 15, "load": 20, "target_rpe": 9, **kw})


def squat_rx(**kw) -> PrescribedExercise:
    return PrescribedExercise(**{"slot_id": "squat", "exercise_id": "back_squat", "sets": 3,
                                 "reps": 5, "load": 225, "target_rpe": 8, **kw})


def make_program(weeks: int = 4, deload_weeks: tuple[int, ...] = ()) -> Program:
    """Two sessions a week: bench + lateral raises on day 1, squat on day 2."""
    return Program(
        units="lb",
        weeks=[
            Week(
                week=w,
                is_deload=w in deload_weeks,
                sessions=[
                    Session(day=1, focus="Upper", exercises=[bench_rx(), lateral_rx()]),
                    Session(day=2, focus="Lower", exercises=[squat_rx()]),
                ],
            )
            for w in range(1, weeks + 1)
        ],
    )


def sets_of(exercise_id: str, week: int, weight: float, reps: int | list[int], rpe: float | None,
            sets: int = 3, day: int = 1, slot_id: str | None = None,
            set_type: str = "working") -> list[SetLog]:
    reps_list = reps if isinstance(reps, list) else [reps] * sets
    return [
        SetLog(week=week, day=day, slot_id=slot_id, exercise_id=exercise_id, set_type=set_type,
               set_number=i, weight=weight, reps=r, rpe=rpe)
        for i, r in enumerate(reps_list, start=1)
    ]


def bench_sets(week: int, weight: float, reps: int | list[int], rpe: float | None, sets: int = 3) -> list[SetLog]:
    return sets_of("bench_press", week, weight, reps, rpe, sets)


def warmups(week: int, exercise_id: str = "bench_press", day: int = 1) -> list[SetLog]:
    return [
        SetLog(week=week, day=day, exercise_id=exercise_id, set_type="warmup",
               set_number=i, weight=w, reps=r)
        for i, (w, r) in enumerate([(45, 10), (95, 5), (135, 3)], start=1)
    ]


@pytest.fixture
def program() -> Program:
    return make_program()
