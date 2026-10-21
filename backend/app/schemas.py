"""Pydantic models: the shape of every request/response.

FastAPI uses these to validate input and to generate the OpenAPI schema the
frontend's TypeScript types are generated from. All weights are in the
program's units (lb or kg).

Identity:
  - A program is one training block (`block`), made of weeks.
  - Each prescribed exercise has a `slot_id`, unique within its session and
    kept the same week to week, so "squat top set" and "squat back-offs" on
    the same day are tracked separately.
  - Logged sets point back to their slot via (block, week, day, slot_id).

Limits: every number must be finite (NaN / Infinity are rejected), weights are capped,
exercise ids must be in the library, and lists and text are bounded so a request
can't be arbitrarily large.
"""

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from app.exercises import Equipment, is_known
from app.rules import Goal

Units = Literal["lb", "kg"]
Experience = Literal["beginner", "intermediate", "advanced"]
SetType = Literal["warmup", "working", "backoff"]

MAX_REPS = 300                      # high enough for timed holds logged as seconds
MAX_LOAD = 2000                     # above any real lift in lb or kg
MAX_LOGS = 20_000                   # ~2 years of logged sets, warm-ups included (all blocks are sent)
MAX_EXERCISES_PER_SESSION = 15
MAX_BLOCK = 1000                    # training blocks; far beyond a lifetime, small enough for JS numbers
MAX_TEXT = 500


class Model(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)


def _known_exercise(exercise_id: str) -> str:
    if not is_known(exercise_id):
        raise ValueError(f"unknown exercise id '{exercise_id}'")
    return exercise_id


ExerciseId = Annotated[str, Field(max_length=64), AfterValidator(_known_exercise)]


# ---------- Profile ----------

class LiftEstimate(Model):
    """A recent set the user can do, used to estimate starting loads."""
    exercise_id: ExerciseId
    weight: float = Field(gt=0, le=MAX_LOAD)
    reps: int = Field(ge=1, le=20)


class Profile(Model):
    goal: Goal
    experience: Experience
    days_per_week: int = Field(ge=2, le=6)
    session_minutes: int = Field(ge=20, le=120)
    equipment: list[Equipment] = Field(min_length=1, max_length=6)
    injuries: str = Field(default="", max_length=MAX_TEXT)
    units: Units = "lb"
    lift_estimates: list[LiftEstimate] = Field(default=[], max_length=20)


# ---------- Program ----------

class PrescribedExercise(Model):
    slot_id: str = Field(
        min_length=1, max_length=40, pattern=r"^[a-z0-9_-]+$",
        description='Unique within the session and stable across weeks, e.g. "squat_top"',
    )
    exercise_id: ExerciseId
    sets: int = Field(ge=1, le=10)
    reps: int = Field(ge=1, le=MAX_REPS, description="Target reps, or the bottom of the rep range")
    reps_max: int | None = Field(
        default=None, ge=1, le=MAX_REPS,
        description="Top of the rep range for double progression (e.g. 3 x 8-12). None = fixed reps.",
    )
    load: float | None = Field(
        default=None, ge=0, le=MAX_LOAD,
        description="0 for bodyweight exercises; None = not known yet, the lifter picks a working "
                    "weight for the rep range on the first session",
    )
    target_rpe: float = Field(ge=5, le=10)
    rest_sec: int = Field(default=120, ge=30, le=600)
    notes: str = Field(default="", max_length=MAX_TEXT)

    @model_validator(mode="after")
    def _rep_range_valid(self):
        if self.reps_max is not None and self.reps_max < self.reps:
            raise ValueError(f"reps_max ({self.reps_max}) must be >= reps ({self.reps})")
        return self


class Session(Model):
    day: int = Field(ge=1, le=7, description="Training day number within the week")
    focus: str = Field(max_length=60)
    exercises: list[PrescribedExercise] = Field(min_length=1, max_length=MAX_EXERCISES_PER_SESSION)

    @model_validator(mode="after")
    def _unique_slots(self):
        ids = [e.slot_id for e in self.exercises]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Duplicate slot_id in day {self.day}")
        return self


class Week(Model):
    week: int = Field(ge=1, le=16)
    is_deload: bool = False
    sessions: list[Session] = Field(min_length=1, max_length=7)

    @model_validator(mode="after")
    def _unique_days(self):
        days = [s.day for s in self.sessions]
        if len(days) != len(set(days)):
            raise ValueError(f"Duplicate day in week {self.week}")
        return self


class Program(Model):
    block: int = Field(default=1, ge=1, le=MAX_BLOCK, description="Training block number; logs carry the same number")
    units: Units = "lb"
    adjusted_through: int = Field(
        default=0, ge=0, le=16,
        description="Last week whose results have been applied to later weeks' prescriptions",
    )
    weeks: list[Week] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def _unique_weeks(self):
        nums = [w.week for w in self.weeks]
        if len(nums) != len(set(nums)):
            raise ValueError("Duplicate week numbers")
        return self


# ---------- Logging ----------

class SetLog(Model):
    id: str | None = Field(default=None, max_length=64, description="Stable client-side id for this row")
    block: int = Field(default=1, ge=1, le=MAX_BLOCK)
    week: int = Field(ge=1, le=16)
    day: int = Field(ge=1, le=7)
    slot_id: str | None = Field(default=None, max_length=40, description="Prescribed slot; None for unplanned sets")
    exercise_id: ExerciseId
    set_type: SetType = "working"
    set_number: int = Field(ge=1, le=20)
    weight: float = Field(ge=0, le=MAX_LOAD)
    reps: int = Field(ge=0, le=MAX_REPS)
    rpe: float | None = Field(default=None, ge=1, le=10)
    completed: bool = True
    note: str = Field(default="", max_length=MAX_TEXT)


# ---------- Metrics (computed in Python, no LLM) ----------

NextStep = Literal["add_load", "add_reps", "hold", "reduce"]


class E1rmPoint(Model):
    week: int
    day: int
    e1rm: float


class Exposure(Model):
    """One slot's working sets in one week."""
    week: int
    load: float = Field(description="Working load (heaviest working set)")
    reps: list[int] = Field(description="Reps of each working set at that load, in set order")
    avg_reps: float
    avg_rpe: float | None
    pr: bool = Field(description="No earlier exposure had >= this load AND >= these average reps")
    deload: bool


class SlotMetrics(Model):
    slot_id: str
    day: int
    exposures: list[Exposure]
    plateau: bool = Field(description="No rep or load PR in recent exposures (hypertrophy-style lifts; "
                                      "strength main lifts use e1RM at the lift level)")
    regressing: bool = Field(description="Performance dropped two exposures in a row")
    in_progress: bool = Field(
        description="The latest session for this slot isn't finished; next_step/suggestions are the "
                    "target for THAT session (from the last finished one) rather than the next session",
    )
    next_step: NextStep
    next_step_reason: str
    suggested_load: float | None = Field(description="None = no working weight known yet; lifter picks one")
    suggested_reps: int | None


class LiftMetrics(Model):
    exercise_id: str
    name: str
    style: Literal["strength", "hypertrophy"]
    kind: Literal["main", "compound", "isolation"]
    e1rm_history: list[E1rmPoint] = Field(description="RPE-adjusted e1RM; only sets with reps + RIR <= 10")
    best_e1rm: float
    avg_rpe_gap: float | None = Field(description="Avg logged RPE minus target. Positive = harder than planned.")
    avg_rir: float | None
    unrated_sets: int = Field(description="Working sets logged without RPE")
    missed_reps: int
    plateau: bool
    plateau_suggestion: str | None
    regressing: bool
    slots: list[SlotMetrics]


VolumeStatus = Literal["below", "in_range", "above", "deload", "no_target"]


class VolumeContribution(Model):
    exercise_id: str
    hard_sets: float


class MuscleVolume(Model):
    week: int
    muscle: str
    hard_sets: float = Field(description="Primary muscle = 1 per hard set, secondary = 0.5")
    contributors: list[VolumeContribution] = Field(description="Which lifts the sets came from, largest first")
    target_low: float | None = Field(description="None for tracked-only muscles (abs, lower back, calves)")
    target_max: float | None
    status: VolumeStatus


class WeekVolume(Model):
    week: int
    is_deload: bool
    tonnage: float = Field(description="Sum of weight x reps for completed non-warm-up sets")
    hard_sets: int = Field(description="Completed non-warm-up sets at 4 RIR or closer (unrated sets count)")
    unrated_sets: int
    avg_rpe_gap: float | None


class DeloadSignal(Model):
    recommended: bool
    scheduled: bool = Field(description="Next week is a scheduled deload")
    next_week: int
    reasons: list[str]


class Metrics(Model):
    goal: Goal
    block: int
    lifts: list[LiftMetrics]
    weekly_volume: list[WeekVolume]
    muscle_volume: list[MuscleVolume]
    sessions_logged: int
    sessions_skipped: list[str] = Field(description='Skipped sessions as "W{week}D{day}"')
    deload: DeloadSignal


class MetricsRequest(Model):
    goal: Goal
    program: Program
    logs: list[SetLog] = Field(max_length=MAX_LOGS)


# ---------- Programs ----------

class TemplateRequest(Model):
    profile: Profile
    block: int = Field(default=1, ge=1, le=MAX_BLOCK, description="Block number for the new program (previous block + 1)")


class TemplateResponse(Model):
    program: Program
    notes: list[str] = Field(description="Guard notes/violations; empty for a healthy template")
    volume_shortfalls: list[str] = Field(
        description="Muscles the schedule can't bring to the starting volume (too few days/minutes)",
    )


class AdvanceResponse(Model):
    program: Program
    changes: list[str] = Field(description="Human-readable prescription changes, e.g. 'W3 D1 bench: 185 -> 190 lb'")
    notes: list[str] = Field(description="Guard notes from checking the updated weeks")


class DemoAthlete(Model):
    profile: Profile
    program: Program
    logs: list[SetLog]
