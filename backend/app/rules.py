"""Training rules: every training number the app uses lives here.

Each goal combines per-exercise rules with volume and deload rules:

  goal          main lifts         compounds / isolation      volume / deload
  strength      STRENGTH_MAIN      double progression         strength
  hypertrophy   double progression double progression         hypertrophy
  general       STRENGTH_MAIN      double progression         hypertrophy   (hybrid)

Every lift uses double progression: keep the same weight and add reps (+1 on
the weakest set) until every set reaches the top of the rep range at target
effort, then add weight and drop back to the bottom of the range. Weight is
never added just because a session went well.

  STRENGTH_MAIN  narrow range (e.g. 3x3-5), weight step +2.5% (min 5 lb / 2.5 kg),
                 progress tracked by RPE-adjusted e1RM
  hypertrophy    wider ranges, weight step = 5 lb / 2.5 kg,
                 progress tracked by rep and load PRs

Effort is expressed as RIR (reps in reserve). RIR = 10 - RPE.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.exercises import ExerciseInfo

Goal = Literal["strength", "hypertrophy", "general"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class Range(_Frozen):
    low: float
    high: float


class ExerciseRules(_Frozen):
    style: Literal["strength", "hypertrophy"]
    reps: Range                      # rep range to prescribe
    reps_limit: Range                # hard limit the guards enforce on AI prescriptions
    rir: Range                       # target reps in reserve
    rep_window: int                  # default top of range = reps + rep_window when no reps_max is set
    load_step_pct: float             # weight increase once the top of the range is hit (min MIN_LOAD_STEP)
    plateau_metric: Literal["e1rm", "performance"]


class VolumeRules(_Frozen):
    start: Range                     # weekly hard sets per muscle at the start of a block
    max: float                       # weekly ceiling per muscle
    ramp: Range                      # sets added per muscle per week when recovering well


class DeloadRules(_Frozen):
    set_cut: float                   # fraction of sets removed
    load_cut: float                  # fraction of load removed
    rir_add: float                   # RIR added to every target


class GoalRules(_Frozen):
    goal: Goal
    main: ExerciseRules
    compound: ExerciseRules
    isolation: ExerciseRules
    volume: VolumeRules
    deload: DeloadRules


class SharedRules(_Frozen):
    min_load_step: dict[str, float]
    hard_set_max_rir: float
    e1rm_max_reps: int
    plateau_window: int
    e1rm_plateau_threshold: float
    rpe_tolerance: float
    reduce_fraction: float
    large_jump_fraction: float
    overshoot_reps: int
    volume_target_muscles: tuple[str, ...]
    deload_every_weeks: int
    max_weekly_increase: float
    max_weekly_decrease: float


# ---------- Per-exercise rules ----------

STRENGTH_MAIN = ExerciseRules(
    style="strength",
    reps=Range(low=1, high=6),
    reps_limit=Range(low=1, high=8),
    rir=Range(low=1, high=3),        # RPE 7-9, no grinding on heavy compounds
    rep_window=2,                    # e.g. 3x3-5, 3x4-6
    load_step_pct=0.025,             # 185 -> 190, 315 -> 325
    plateau_metric="e1rm",
)

HYPERTROPHY_COMPOUND = ExerciseRules(
    style="hypertrophy",
    reps=Range(low=6, high=10),
    reps_limit=Range(low=5, high=30),  # ~5-30 reps all grow muscle when taken near failure
    rir=Range(low=1, high=3),
    rep_window=4,                    # e.g. 6-10, 8-12
    load_step_pct=0.0,               # smallest step: 5 lb / 2.5 kg
    plateau_metric="performance",
)

HYPERTROPHY_ISOLATION = ExerciseRules(
    style="hypertrophy",
    reps=Range(low=10, high=20),
    reps_limit=Range(low=5, high=30),
    rir=Range(low=0, high=2),
    rep_window=5,                    # e.g. 10-15, 12-17
    load_step_pct=0.0,
    plateau_metric="performance",
)

# ---------- Volume and deload rules ----------

STRENGTH_VOLUME = VolumeRules(start=Range(low=6, high=10), max=15, ramp=Range(low=0, high=1))
HYPERTROPHY_VOLUME = VolumeRules(start=Range(low=10, high=12), max=20, ramp=Range(low=1, high=2))

STRENGTH_DELOAD = DeloadRules(set_cut=0.4, load_cut=0.10, rir_add=2)
HYPERTROPHY_DELOAD = DeloadRules(set_cut=0.5, load_cut=0.0, rir_add=3)  # keep load, cut sets + effort

GOALS: dict[str, GoalRules] = {
    "strength": GoalRules(
        goal="strength", main=STRENGTH_MAIN, compound=HYPERTROPHY_COMPOUND,
        isolation=HYPERTROPHY_ISOLATION, volume=STRENGTH_VOLUME, deload=STRENGTH_DELOAD,
    ),
    "hypertrophy": GoalRules(
        goal="hypertrophy", main=HYPERTROPHY_COMPOUND, compound=HYPERTROPHY_COMPOUND,
        isolation=HYPERTROPHY_ISOLATION, volume=HYPERTROPHY_VOLUME, deload=HYPERTROPHY_DELOAD,
    ),
    "general": GoalRules(
        goal="general", main=STRENGTH_MAIN, compound=HYPERTROPHY_COMPOUND,
        isolation=HYPERTROPHY_ISOLATION, volume=HYPERTROPHY_VOLUME, deload=HYPERTROPHY_DELOAD,
    ),
}

# ---------- Shared constants ----------

# Smallest load jump: 5 lb (a 2.5 lb plate per side, or the next dumbbell up)
# or 2.5 kg. All loads are rounded to multiples of this, and every progression
# or cap allows at least one step so light lifts can still move.
MIN_LOAD_STEP: dict[str, float] = {"lb": 5.0, "kg": 2.5}

HARD_SET_MAX_RIR = 4                # a set counts toward volume at 4 RIR or closer to failure
E1RM_MAX_REPS = 10                   # Epley is unreliable above ~10 reps (reps + RIR)
PLATEAU_WINDOW = 3                   # no progress across this many recent sessions/weeks
E1RM_PLATEAU_THRESHOLD = 0.01        # e1RM gains under 1% count as noise
RPE_TOLERANCE = 0.5                  # avg RPE may exceed target by this much and still progress
REDUCE_FRACTION = 0.075              # "reduce" = ~5-10% load cut (min one MIN_LOAD_STEP)

# Small lifts: when the next load step is a big relative jump (20 -> 25 lb is +25%),
# build reps past the top of the range first so the new weight lands near the range,
# and after any jump allow working below the range while reps keep improving.
LARGE_JUMP_FRACTION = 0.10           # a step bigger than this share of the load is a "large jump"
OVERSHOOT_REPS = 3                   # reps above the top of the range required before a large jump

# Muscles with weekly volume targets. Abs, lower back and calves are tracked but get
# no "below target" warning (mostly trained indirectly or optional for most lifters).
VOLUME_TARGET_MUSCLES: tuple[str, ...] = (
    "chest", "lats", "upper_back", "front_delts", "side_delts", "rear_delts",
    "triceps", "biceps", "quads", "hamstrings", "glutes",
)

DELOAD_EVERY_WEEKS = 5               # scheduled: every 5th week since the last deload
DELOAD_REGRESSION_MIN_LIFTS = 2      # reactive: performance dropping on at least 2 lifts...
DELOAD_REGRESSION_FRACTION = 1 / 3   # ...and at least 1/3 of main + compound lifts
DELOAD_PLATEAU_FRACTION = 0.5        # reactive: half of main + compound lifts plateaued
DELOAD_EFFORT_GAP = 1.0              # reactive: avg RPE this far over target...
DELOAD_EFFORT_WEEKS = 2              # ...for this many consecutive weeks

MAX_WEEKLY_INCREASE = 0.05           # load guard: max +5% per week (min one MIN_LOAD_STEP)
MAX_WEEKLY_DECREASE = 0.20           # load guard: max -20% per week

SHARED = SharedRules(
    min_load_step=MIN_LOAD_STEP,
    hard_set_max_rir=HARD_SET_MAX_RIR,
    e1rm_max_reps=E1RM_MAX_REPS,
    plateau_window=PLATEAU_WINDOW,
    e1rm_plateau_threshold=E1RM_PLATEAU_THRESHOLD,
    rpe_tolerance=RPE_TOLERANCE,
    reduce_fraction=REDUCE_FRACTION,
    large_jump_fraction=LARGE_JUMP_FRACTION,
    overshoot_reps=OVERSHOOT_REPS,
    volume_target_muscles=VOLUME_TARGET_MUSCLES,
    deload_every_weeks=DELOAD_EVERY_WEEKS,
    max_weekly_increase=MAX_WEEKLY_INCREASE,
    max_weekly_decrease=MAX_WEEKLY_DECREASE,
)


def goal_rules(goal: str) -> GoalRules:
    return GOALS[goal]


def rules_for_exercise(goal: str, exercise: ExerciseInfo) -> ExerciseRules:
    return getattr(GOALS[goal], exercise.kind)


def target_rpe_range(rules: ExerciseRules) -> Range:
    return Range(low=10 - rules.rir.high, high=10 - rules.rir.low)


def top_of_range(rules: ExerciseRules, reps: int, reps_max: int | None) -> int:
    """Top of the rep range: the prescribed reps_max, else reps + the default window."""
    if reps_max is not None:
        return reps_max
    return min(reps + rules.rep_window, int(rules.reps_limit.high))


def rpe_to_rir(rpe: float) -> float:
    return 10 - rpe
