"""Training math. Pure functions, no LLM.

Every number comes from the rules in rules.py. These metrics are injected into
prompts so the LLM reasons about trends instead of doing arithmetic, and the
frontend charts reuse them.

Set types:
  warmup   ignored everywhere (except marking the session as logged)
  working  counts for volume, effort, e1RM and progression decisions
  backoff  counts for volume, effort and e1RM, but not progression decisions
"""

import math
from collections import defaultdict
from statistics import mean

from app.exercises import ExerciseInfo, get_exercise, muscle_credits
from app.rules import (
    DELOAD_EFFORT_GAP,
    DELOAD_EFFORT_WEEKS,
    DELOAD_EVERY_WEEKS,
    DELOAD_PLATEAU_FRACTION,
    DELOAD_REGRESSION_FRACTION,
    DELOAD_REGRESSION_MIN_LIFTS,
    E1RM_MAX_REPS,
    E1RM_PLATEAU_THRESHOLD,
    HARD_SET_MAX_RIR,
    MIN_LOAD_STEP,
    PLATEAU_WINDOW,
    REDUCE_FRACTION,
    RPE_TOLERANCE,
    ExerciseRules,
    Goal,
    goal_rules,
    rpe_to_rir,
    top_of_range,
    rules_for_exercise,
)
from app.schemas import (
    DeloadSignal,
    E1rmPoint,
    Exposure,
    LiftMetrics,
    Metrics,
    MuscleVolume,
    NextStep,
    PrescribedExercise,
    Program,
    SetLog,
    SlotMetrics,
    Units,
    VolumeStatus,
    WeekVolume,
)

SlotKey = tuple[int, str]           # (day, slot_id)

TIMED_STEP_SECONDS = 5               # timed holds progress 5 s at a time, not 1


# ---------- Basic formulas ----------

def epley_e1rm(weight: float, reps: float) -> float:
    """Estimated one-rep max (Epley formula)."""
    if weight <= 0 or reps <= 0:
        return 0.0
    if reps <= 1:
        return round(weight, 1)
    return round(weight * (1 + reps / 30), 1)


def set_e1rm(weight: float, reps: int, rpe: float | None) -> float | None:
    """RPE-adjusted e1RM: a set of 5 @ RPE 8 is treated as a 7-rep max.

    Returns None when reps + RIR exceeds the range where Epley is reliable.
    """
    effective = reps + (rpe_to_rir(rpe) if rpe is not None else 0)
    if effective > E1RM_MAX_REPS:
        return None
    return epley_e1rm(weight, effective)


def round_to_increment(load: float, units: Units) -> float:
    """Round to the nearest loadable weight (multiples of 5 lb / 2.5 kg)."""
    inc = MIN_LOAD_STEP[units]
    return round(round(load / inc) * inc, 2)


def floor_to_increment(load: float, units: Units) -> float:
    """Round down to a loadable weight (tolerant of float error: 224.9999 -> 225, not 220)."""
    inc = MIN_LOAD_STEP[units]
    return math.floor(load / inc + 1e-9) * inc


def is_done(log: SetLog) -> bool:
    return log.completed and log.reps > 0


def is_hard_set(log: SetLog) -> bool:
    """Completed non-warm-up set close enough to failure to drive growth. Unrated sets count."""
    return (
        log.set_type != "warmup"
        and is_done(log)
        and (log.rpe is None or rpe_to_rir(log.rpe) <= HARD_SET_MAX_RIR)
    )


# ---------- Plateau / PRs / regression ----------

def detect_e1rm_plateau(values: list[float], window: int = PLATEAU_WINDOW,
                        threshold: float = E1RM_PLATEAU_THRESHOLD) -> bool:
    """Best e1RM over the last `window` points isn't meaningfully above the earlier best."""
    if len(values) <= window:
        return False
    return max(values[-window:]) <= max(values[:-window]) * (1 + threshold)


def mark_prs(points: list[tuple[float, float]]) -> list[bool]:
    """A (load, avg_reps) exposure is a PR if no earlier exposure had >= load AND >= reps.

    More reps at the same load, or more load for the same reps, both count.
    """
    return [
        not any(l >= load and r >= reps for l, r in points[:i])
        for i, (load, reps) in enumerate(points)
    ]


def detect_performance_plateau(pr_flags: list[bool], window: int = PLATEAU_WINDOW) -> bool:
    """No load PR and no rep PR across the last `window` exposures."""
    if len(pr_flags) <= window:
        return False
    return not any(pr_flags[-window:])


def detect_regression(scores: list[float]) -> bool:
    """Performance dropped two exposures in a row."""
    return len(scores) >= 3 and scores[-3] > scores[-2] > scores[-1]


# ---------- Progression decision for one slot ----------

def _working(sets: list[SetLog], rx: PrescribedExercise) -> list[SetLog]:
    """Prescribed working sets only (ignores extra sets beyond rx.sets)."""
    return [s for s in sets if s.set_type == "working" and s.set_number <= rx.sets]


def is_short(sets: list[SetLog], rx: PrescribedExercise) -> bool:
    """A logged working set failed or fell below the prescribed (bottom) reps."""
    return any(not s.completed or s.reps < rx.reps for s in _working(sets, rx))


def _avg_rpe(sets: list[SetLog]) -> float | None:
    rated = [s.rpe for s in sets if s.rpe is not None]
    return mean(rated) if rated else None


def suggest_next_step(
    rules: ExerciseRules,
    info: ExerciseInfo,
    rx: PrescribedExercise,
    sets: list[SetLog],
    units: Units,
    short_last_time: bool = False,
) -> tuple[NextStep, str, float, int]:
    """Decide the next session for one slot from its latest sets (double progression).

    Same weight, +1 rep on the weakest set, until every set reaches the top of
    the range at target effort; only then add weight and reset to the bottom.

    Returns (step, reason, suggested_load, suggested_reps).
    `short_last_time` is whether the previous exposure was also short.
    """
    inc = MIN_LOAD_STEP[units]
    working = [s for s in _working(sets, rx) if is_done(s)]
    load = max((s.weight for s in working), default=rx.load or 0.0)
    bodyweight = info.equipment == "bodyweight" and not rx.load
    avg_rpe = _avg_rpe(working)
    max_rpe = rx.target_rpe + RPE_TOLERANCE
    top = top_of_range(rules, rx.reps, rx.reps_max)
    short = is_short(sets, rx)

    if short and short_last_time:
        if bodyweight:
            return "reduce", "Missed reps two sessions in a row; use an easier variation or fewer reps.", 0.0, rx.reps
        new = min(round_to_increment(load * (1 - REDUCE_FRACTION), units), load - inc)
        return "reduce", f"Missed reps two sessions in a row; drop to {max(new, 0):g} {units}.", max(new, 0.0), rx.reps

    if rx.load and load < rx.load:
        return "hold", (
            f"Worked at {load:g} {units}, below the prescribed {rx.load:g}; repeat the prescribed load."
        ), rx.load, rx.reps

    if short:
        return "hold", f"Missed prescribed reps; repeat {load:g} {units}.", load, rx.reps

    if len(working) < rx.sets:
        return "hold", f"Only {len(working)} of {rx.sets} sets logged; repeat {load:g} {units}.", load, rx.reps

    if all(s.reps >= top for s in working):
        if avg_rpe is not None and avg_rpe > max_rpe:
            return "hold", (
                f"Hit {top} reps on every set but avg RPE {avg_rpe:.1f} is over target {rx.target_rpe:g}; "
                f"repeat until the top reps feel like RPE {rx.target_rpe:g}."
            ), load, top
        if bodyweight:
            return "add_load", (
                f"Hit {top} reps on every set; add external load or move to a harder variation."
            ), 0.0, rx.reps
        new = max(round_to_increment(load * (1 + rules.load_step_pct), units), load + inc)
        return "add_load", (
            f"Hit the top of {rx.reps}-{top} on every set at target effort; "
            f"increase to {new:g} {units} and reset to {rx.reps} reps."
        ), new, rx.reps

    weakest = min(s.reps for s in working)
    if avg_rpe is not None and avg_rpe > max_rpe:
        return "hold", (
            f"Avg RPE {avg_rpe:.1f} is over target {rx.target_rpe:g}; repeat {weakest}+ reps at "
            f"{load:g} {units} until it feels like RPE {rx.target_rpe:g}, then add reps."
        ), load, weakest

    if info.timed:
        target = min(weakest + TIMED_STEP_SECONDS, top)
        return "add_reps", (
            f"Aim for {target}s on your shortest hold. Move to a harder variation once every hold reaches {top}s."
        ), load, target

    target = min(weakest + 1, top)
    return "add_reps", (
        f"Stay at {load:g} {units}; aim for {target} reps on your weakest set. "
        f"Add weight once every set reaches {top}."
    ), load, target


# ---------- Metrics ----------

def _info(exercise_id: str) -> ExerciseInfo:
    return get_exercise(exercise_id) or ExerciseInfo(
        id=exercise_id, name=exercise_id, equipment="bodyweight", kind="compound", primary=()
    )


class _Index:
    """Lookup of prescriptions by (week, day, slot_id)."""

    def __init__(self, program: Program):
        self.rx: dict[tuple[int, int, str], PrescribedExercise] = {}
        self.by_exercise: dict[tuple[int, int, str], list[str]] = defaultdict(list)
        for w in program.weeks:
            for s in w.sessions:
                for ex in s.exercises:
                    self.rx[(w.week, s.day, ex.slot_id)] = ex
                    self.by_exercise[(w.week, s.day, ex.exercise_id)].append(ex.slot_id)

    def resolve(self, log: SetLog) -> tuple[str, PrescribedExercise] | None:
        """Find the slot a logged set belongs to. Unknown or ambiguous -> None."""
        slot_id = log.slot_id
        if slot_id is None:
            candidates = self.by_exercise.get((log.week, log.day, log.exercise_id), [])
            if len(candidates) != 1:
                return None
            slot_id = candidates[0]
        rx = self.rx.get((log.week, log.day, slot_id))
        if rx is None or rx.exercise_id != log.exercise_id:
            return None
        return slot_id, rx


def _slot_metrics(
    key: SlotKey,
    by_week: dict[int, list[SetLog]],
    rx_by_week: dict[int, PrescribedExercise],
    rules: ExerciseRules,
    info: ExerciseInfo,
    units: Units,
    deload_weeks: set[int],
) -> SlotMetrics:
    exposures: list[Exposure] = []
    for week in sorted(by_week):
        done = [s for s in _working(by_week[week], rx_by_week[week]) if is_done(s)]
        if not done:
            continue
        load = max(s.weight for s in done)
        reps = [s.reps for s in sorted(done, key=lambda s: s.set_number) if s.weight == load]
        exposures.append(Exposure(week=week, load=load, reps=reps, avg_reps=round(mean(reps), 2),
                                  pr=False, deload=week in deload_weeks))

    trend = [e for e in exposures if not e.deload]
    for e, pr in zip(trend, mark_prs([(e.load, e.avg_reps) for e in trend])):
        e.pr = pr

    plateau = rules.plateau_metric == "performance" and detect_performance_plateau([e.pr for e in trend])
    regressing = detect_regression([e.load * (1 + e.avg_reps / 30) for e in trend])

    weeks_logged = sorted(by_week)
    if not weeks_logged:
        step, reason, load, reps = "hold", "No sets logged yet.", None, None
    else:
        last = weeks_logged[-1]
        rx = rx_by_week[last]
        if last in deload_weeks:
            step, reason, load, reps = "hold", "Deload week; resume progression next week.", rx.load, rx.reps
        else:
            hard_weeks = [w for w in weeks_logged if w not in deload_weeks]
            prev = hard_weeks[-2] if len(hard_weeks) >= 2 else None
            short_before = prev is not None and is_short(by_week[prev], rx_by_week[prev])
            step, reason, load, reps = suggest_next_step(
                rules, info, rx, by_week[last], units, short_before
            )

    return SlotMetrics(
        slot_id=key[1], day=key[0], exposures=exposures, plateau=plateau, regressing=regressing,
        next_step=step, next_step_reason=reason, suggested_load=load, suggested_reps=reps,
    )


def _volume_status(hard_sets: float, low: float, high: float, deload: bool) -> VolumeStatus:
    if deload:
        return "deload"
    if hard_sets < low:
        return "below"
    if hard_sets > high:
        return "above"
    return "in_range"


def compute_metrics(goal: Goal, program: Program, logs: list[SetLog]) -> Metrics:
    index = _Index(program)
    units = program.units
    deload_weeks = {w.week for w in program.weeks if w.is_deload}
    volume_rules = goal_rules(goal).volume

    logged_sessions: set[tuple[int, int]] = set()
    tonnage: dict[int, float] = defaultdict(float)
    hard_sets: dict[int, int] = defaultdict(int)
    unrated: dict[int, int] = defaultdict(int)
    week_gaps: dict[int, list[float]] = defaultdict(list)
    muscle_sets: dict[tuple[int, str], float] = defaultdict(float)

    # Per exercise
    e1rm_points: dict[str, dict[tuple[int, int], float]] = defaultdict(dict)
    rpes: dict[str, list[float]] = defaultdict(list)
    gaps: dict[str, list[float]] = defaultdict(list)
    lift_unrated: dict[str, int] = defaultdict(int)
    missed: dict[str, int] = defaultdict(int)
    seen: set[str] = set()

    # Per slot
    slot_sets: dict[str, dict[SlotKey, dict[int, list[SetLog]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list)))
    slot_rx: dict[SlotKey, dict[int, PrescribedExercise]] = defaultdict(dict)

    for log in logs:
        if log.block != program.block:
            continue
        logged_sessions.add((log.week, log.day))
        if log.set_type == "warmup":
            continue

        lift = log.exercise_id
        seen.add(lift)
        resolved = index.resolve(log)
        rx = resolved[1] if resolved else None

        if resolved:
            key = (log.day, resolved[0])
            slot_sets[lift][key][log.week].append(log)
            slot_rx[key][log.week] = rx

        if is_done(log):
            tonnage[log.week] += log.weight * log.reps
            e1rm = set_e1rm(log.weight, log.reps, log.rpe)
            if e1rm:
                k = (log.week, log.day)
                e1rm_points[lift][k] = max(e1rm_points[lift].get(k, 0.0), e1rm)
            if log.rpe is None:
                unrated[log.week] += 1
                lift_unrated[lift] += 1
            else:
                rpes[lift].append(log.rpe)
                if rx is not None:
                    gaps[lift].append(log.rpe - rx.target_rpe)
                    week_gaps[log.week].append(log.rpe - rx.target_rpe)

        if is_hard_set(log):
            hard_sets[log.week] += 1
            for muscle, credit in muscle_credits(lift).items():
                muscle_sets[(log.week, muscle)] += credit

        if rx is not None and log.set_type == "working" and log.set_number <= rx.sets:
            missed[lift] += max(0, rx.reps - (log.reps if log.completed else 0))

    lifts: list[LiftMetrics] = []
    for lift in sorted(seen):
        info = _info(lift)
        rules = rules_for_exercise(goal, info)
        history = [E1rmPoint(week=w, day=d, e1rm=v) for (w, d), v in sorted(e1rm_points[lift].items())]
        slots = [
            _slot_metrics(key, by_week, slot_rx[key], rules, info, units, deload_weeks)
            for key, by_week in sorted(slot_sets[lift].items())
        ]

        if rules.plateau_metric == "e1rm":
            weekly_best: dict[int, float] = {}
            for p in history:
                if p.week not in deload_weeks:
                    weekly_best[p.week] = max(weekly_best.get(p.week, 0.0), p.e1rm)
            plateau = detect_e1rm_plateau([weekly_best[w] for w in sorted(weekly_best)])
        else:
            plateau = any(s.plateau for s in slots)

        lifts.append(LiftMetrics(
            exercise_id=lift,
            name=info.name,
            style=rules.style,
            kind=info.kind,
            e1rm_history=history,
            best_e1rm=max((p.e1rm for p in history), default=0.0),
            avg_rpe_gap=round(mean(gaps[lift]), 2) if gaps[lift] else None,
            avg_rir=round(mean(rpe_to_rir(r) for r in rpes[lift]), 2) if rpes[lift] else None,
            unrated_sets=lift_unrated[lift],
            missed_reps=missed[lift],
            plateau=plateau,
            plateau_suggestion=(
                f"No progress in {PLATEAU_WINDOW} sessions: swap to a close variation or change "
                "the rep range for this lift." if plateau else None
            ),
            regressing=any(s.regressing for s in slots),
            slots=slots,
        ))

    weeks = sorted({w for w, _ in logged_sessions})
    weekly = [
        WeekVolume(
            week=w,
            is_deload=w in deload_weeks,
            tonnage=round(tonnage[w], 1),
            hard_sets=hard_sets[w],
            unrated_sets=unrated[w],
            avg_rpe_gap=round(mean(week_gaps[w]), 2) if week_gaps[w] else None,
        )
        for w in weeks
    ]

    muscle_volume = [
        MuscleVolume(
            week=w, muscle=m, hard_sets=v,
            target_low=volume_rules.start.low, target_max=volume_rules.max,
            status=_volume_status(v, volume_rules.start.low, volume_rules.max, w in deload_weeks),
        )
        for (w, m), v in sorted(muscle_sets.items())
    ]

    # A prescribed session counts as skipped if it has no logs but a later one does.
    skipped: list[str] = []
    if logged_sessions:
        last = max(logged_sessions)
        skipped = [
            f"W{w.week}D{s.day}"
            for w in program.weeks for s in w.sessions
            if (w.week, s.day) < last and (w.week, s.day) not in logged_sessions
        ]

    return Metrics(
        goal=goal,
        block=program.block,
        lifts=lifts,
        weekly_volume=weekly,
        muscle_volume=muscle_volume,
        sessions_logged=len(logged_sessions),
        sessions_skipped=skipped,
        deload=deload_signal(lifts, weekly, deload_weeks),
    )


def deload_signal(lifts: list[LiftMetrics], weekly: list[WeekVolume], deload_weeks: set[int]) -> DeloadSignal:
    """Scheduled (every 5th week since the last deload) or reactive (systemic fatigue)."""
    next_week = weekly[-1].week + 1 if weekly else 1
    last_deload = max((w for w in deload_weeks if w < next_week), default=0)
    scheduled = next_week in deload_weeks or next_week - last_deload >= DELOAD_EVERY_WEEKS

    reasons: list[str] = []
    if scheduled:
        reasons.append(f"Week {next_week} is a scheduled deload (every {DELOAD_EVERY_WEEKS} weeks).")

    just_deloaded = bool(weekly) and weekly[-1].is_deload
    if not just_deloaded:
        # Plateaus on single lifts are handled per lift; only systemic signals on
        # main + compound lifts trigger a whole-program deload.
        systemic = [l for l in lifts if l.kind != "isolation"]
        regressing = [l.name for l in systemic if l.regressing]
        if len(regressing) >= max(DELOAD_REGRESSION_MIN_LIFTS, DELOAD_REGRESSION_FRACTION * len(systemic)):
            reasons.append(f"Performance dropping on {len(regressing)} lifts: {', '.join(regressing)}.")

        plateaued = [l.name for l in systemic if l.plateau]
        if len(plateaued) >= 2 and len(plateaued) >= DELOAD_PLATEAU_FRACTION * len(systemic):
            reasons.append(f"Plateau on {len(plateaued)} of {len(systemic)} main/compound lifts.")

        hard_weeks = [w for w in weekly if not w.is_deload][-DELOAD_EFFORT_WEEKS:]
        if len(hard_weeks) == DELOAD_EFFORT_WEEKS and all(
            w.avg_rpe_gap is not None and w.avg_rpe_gap >= DELOAD_EFFORT_GAP for w in hard_weeks
        ):
            reasons.append(
                f"Effort {DELOAD_EFFORT_GAP:g}+ RPE over target for {DELOAD_EFFORT_WEEKS} weeks running."
            )

    return DeloadSignal(recommended=bool(reasons), scheduled=scheduled, next_week=next_week, reasons=reasons)
