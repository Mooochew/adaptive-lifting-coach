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
    LARGE_JUMP_FRACTION,
    MIN_LOAD_STEP,
    OVERSHOOT_REPS,
    PLATEAU_WINDOW,
    REDUCE_FRACTION,
    RPE_TOLERANCE,
    VOLUME_TARGET_MUSCLES,
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
    VolumeContribution,
    VolumeStatus,
    WeekVolume,
)

SlotKey = tuple[int, str]           # (day, slot_id)

TIMED_STEP_SECONDS = 5               # timed holds progress 5 s at a time, not 1

Decision = tuple[NextStep, str, float | None, int | None]


# ---------- Basic formulas ----------

def epley_e1rm(weight: float, reps: float) -> float:
    """Estimated one-rep max (Epley formula)."""
    if weight <= 0 or reps <= 0:
        return 0.0
    if reps <= 1:
        return round(weight, 1)
    return round(weight * (1 + reps / 30), 1)


def set_e1rm(weight: float, reps: int, rpe: float | None, assumed_rpe: float | None = None) -> float | None:
    """RPE-adjusted e1RM: a set of 5 @ RPE 8 is treated as a 7-rep max.

    Unrated sets use `assumed_rpe` (the prescription's target) when given, else count
    as all-out. Returns None when reps + RIR exceeds the range where Epley is reliable.
    """
    effort = rpe if rpe is not None else assumed_rpe
    effective = reps + (rpe_to_rir(effort) if effort is not None else 0)
    if effective > E1RM_MAX_REPS:
        return None
    return epley_e1rm(weight, effective)


def load_from_recent_set(weight: float, reps: int, target_reps: int, target_rpe: float, units: Units) -> float:
    """Working load for `target_reps` at `target_rpe`, from a recent set of `weight` x `reps`.

    Unrounded Epley both ways (epley_e1rm rounds for display, which can cost a whole
    load step), rounded DOWN so starting loads stay conservative.
    """
    e1rm = weight * (1 + reps / 30) if reps > 1 else weight
    load = e1rm / (1 + (target_reps + rpe_to_rir(target_rpe)) / 30)
    return floor_to_increment(load, units)


def round_to_increment(load: float, units: Units) -> float:
    """Round to the nearest loadable weight (multiples of 5 lb / 2.5 kg)."""
    inc = MIN_LOAD_STEP[units]
    return round(round(load / inc) * inc, 2)


def floor_to_increment(load: float, units: Units) -> float:
    """Round down to a loadable weight (tolerant of float error: 224.9999 -> 225, not 220)."""
    inc = MIN_LOAD_STEP[units]
    return math.floor(load / inc + 1e-9) * inc


def next_load(load: float, rules: ExerciseRules, units: Units) -> float:
    """Load after a successful top-of-range: +load_step_pct rounded DOWN, but at least one min step."""
    inc = MIN_LOAD_STEP[units]
    return max(floor_to_increment(load * (1 + rules.load_step_pct), units), load + inc)


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


def is_drop(before: tuple[float, float, float | None], after: tuple[float, float, float | None]) -> bool:
    """`after` is clearly worse than `before`: fewer reps at no more load, less load for no more
    reps, or the same performance at least 1 RPE harder. A planned jump (more load, fewer
    reps) is NOT a drop."""
    (l0, r0, e0), (l1, r1, e1) = before, after
    if l1 <= l0 and r1 < r0:
        return True
    if l1 < l0 and r1 <= r0:
        return True
    return l1 == l0 and r1 == r0 and e0 is not None and e1 is not None and e1 - e0 >= 1


def detect_regression(points: list[tuple[float, float, float | None]]) -> bool:
    """Performance dropped two exposures in a row (points are (load, avg_reps, avg_rpe))."""
    return len(points) >= 3 and is_drop(points[-3], points[-2]) and is_drop(points[-2], points[-1])


# ---------- Progression decision for one slot ----------

def _working(sets: list[SetLog], rx: PrescribedExercise) -> list[SetLog]:
    """Prescribed working sets only (ignores extra sets beyond rx.sets)."""
    return [s for s in sets if s.set_type == "working" and s.set_number <= rx.sets]


def is_short(sets: list[SetLog], rx: PrescribedExercise) -> bool:
    """A logged working set failed or fell below the prescribed (bottom) reps."""
    return any(not s.completed or s.reps < rx.reps for s in _working(sets, rx))


def is_logged_in_full(sets: list[SetLog], rx: PrescribedExercise) -> bool:
    """Every prescribed working set has been logged (done or failed)."""
    return len({s.set_number for s in _working(sets, rx)}) >= rx.sets


def _avg_rpe(sets: list[SetLog]) -> float | None:
    rated = [s.rpe for s in sets if s.rpe is not None]
    return mean(rated) if rated else None


def _performance(sets: list[SetLog], rx: PrescribedExercise) -> tuple[float, float] | None:
    """(working load, avg reps at that load) for completed working sets."""
    done = [s for s in _working(sets, rx) if is_done(s)]
    if not done:
        return None
    load = max(s.weight for s in done)
    return load, mean(s.reps for s in done if s.weight == load)


def improved(prev: tuple[float, float] | None, cur: tuple[float, float] | None) -> bool:
    """More load than last time, or more reps at the same load."""
    if prev is None or cur is None:
        return False
    return cur[0] > prev[0] or (cur[0] == prev[0] and cur[1] > prev[1])


def suggest_next_step(
    rules: ExerciseRules,
    info: ExerciseInfo,
    rx: PrescribedExercise,
    sets: list[SetLog],
    units: Units,
    prev_sets: list[SetLog] | None = None,
    prev_rx: PrescribedExercise | None = None,
) -> Decision:
    """Decide the next session for one slot from its latest sets (double progression).

    Same weight, +1 rep on the weakest set, until every set reaches the top of
    the range at target effort; only then add weight and reset to the bottom.
    If that weight step is a large relative jump (small lifts), first build
    OVERSHOOT_REPS past the top. After a jump, sessions below the range are fine
    as long as reps keep improving; "reduce" only fires when short twice AND stuck.

    `prev_sets`/`prev_rx` are the previous non-deload exposure of the same slot.
    Returns (step, reason, suggested_load, suggested_reps); load None = pick a weight.
    """
    inc = MIN_LOAD_STEP[units]
    working = [s for s in _working(sets, rx) if is_done(s)]
    top = top_of_range(rules, rx.reps, rx.reps_max)

    if not working and not rx.load:
        return "hold", (
            f"Pick a working weight you can do for {rx.reps}-{top} reps at RPE {rx.target_rpe:g}."
        ), None, rx.reps

    load = max((s.weight for s in working), default=rx.load or 0.0)
    bodyweight = info.equipment == "bodyweight" and load == 0
    avg_rpe = _avg_rpe(working)
    max_rpe = rx.target_rpe + RPE_TOLERANCE
    weakest = min((s.reps for s in working), default=0)
    short = is_short(sets, rx)

    prev_perf = _performance(prev_sets, prev_rx) if prev_sets and prev_rx else None
    cur_perf = _performance(sets, rx)
    getting_better = improved(prev_perf, cur_perf)
    short_before = bool(prev_sets and prev_rx and is_short(prev_sets, prev_rx))

    if short and short_before and not getting_better:
        if bodyweight:
            return "reduce", (
                "Missed reps two sessions in a row without improving; use an easier variation or fewer reps."
            ), 0.0, rx.reps
        new = max(min(round_to_increment(load * (1 - REDUCE_FRACTION), units), load - inc), 0.0)
        return "reduce", (
            f"Missed reps two sessions in a row without improving; drop to {new:g} {units}."
        ), new, rx.reps

    if rx.load and load < rx.load:
        return "hold", (
            f"Worked at {load:g} {units}, below the prescribed {rx.load:g}; repeat the prescribed load."
        ), rx.load, rx.reps

    if short:
        if getting_better and working:
            target = min(weakest + 1, top)
            return "add_reps", (
                f"Below the {rx.reps}-{top} range but improving; stay at {load:g} {units} and aim for "
                f"{target} reps on your weakest set."
            ), load, target
        return "hold", f"Missed prescribed reps; repeat {load:g} {units}.", load, rx.reps

    if len(working) < rx.sets:
        return "hold", f"Only {len(working)} of {rx.sets} sets logged; repeat {load:g} {units}.", load, rx.reps

    # How far reps must go before a weight jump (overshoot for large relative jumps).
    jump_top = top
    new = next_load(load, rules, units)
    if not info.timed and not bodyweight and load > 0 and (new - load) / load > LARGE_JUMP_FRACTION:
        jump_top = min(top + OVERSHOOT_REPS, int(rules.reps_limit.high))

    if all(s.reps >= jump_top for s in working):
        if avg_rpe is not None and avg_rpe > max_rpe:
            return "hold", (
                f"Hit {jump_top} reps on every set but avg RPE {avg_rpe:.1f} is over target {rx.target_rpe:g}; "
                f"repeat until the top reps feel like RPE {rx.target_rpe:g}."
            ), load, jump_top
        if bodyweight:
            return "add_load", (
                f"Hit {top} reps on every set; add external load or move to a harder variation."
            ), 0.0, rx.reps
        if info.timed:
            return "add_load", (
                f"Every hold reached {top}s; move to a harder variation."
            ), load, rx.reps
        return "add_load", (
            f"Hit {jump_top} reps on every set at target effort; increase to {new:g} {units} and "
            f"reset to {rx.reps} reps."
        ), new, rx.reps

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

    target = min(weakest + 1, jump_top)
    if jump_top > top and weakest >= top:
        return "add_reps", (
            f"Top of the range reached, but the next step ({load:g} -> {new:g} {units}) is a "
            f"{(new - load) / load:.0%} jump. Build to {jump_top} reps on every set first; aim for {target}."
        ), load, target
    return "add_reps", (
        f"Stay at {load:g} {units}; aim for {target} reps on your weakest set. "
        f"Add weight once every set reaches {jump_top}."
    ), load, target


# ---------- Metrics ----------

def _info(exercise_id: str) -> ExerciseInfo:
    return get_exercise(exercise_id) or ExerciseInfo(
        id=exercise_id, name=exercise_id, equipment="bodyweight", kind="compound", primary=()
    )


class PrescriptionIndex:
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
        rx = rx_by_week[week]
        done = [s for s in _working(by_week[week], rx) if is_done(s)]
        if not done:
            continue
        load = max(s.weight for s in done)
        at_load = [s for s in sorted(done, key=lambda s: s.set_number) if s.weight == load]
        avg_rpe = _avg_rpe(at_load)
        exposures.append(Exposure(
            week=week, load=load, reps=[s.reps for s in at_load],
            avg_reps=round(mean(s.reps for s in at_load), 2),
            avg_rpe=round(avg_rpe, 2) if avg_rpe is not None else None,
            pr=False, deload=week in deload_weeks,
        ))

    trend = [e for e in exposures if not e.deload]
    for e, pr in zip(trend, mark_prs([(e.load, e.avg_reps) for e in trend])):
        e.pr = pr

    plateau = rules.plateau_metric == "performance" and detect_performance_plateau([e.pr for e in trend])
    regressing = detect_regression([(e.load, e.avg_reps, e.avg_rpe) for e in trend])

    def decide(week: int, prev: int | None) -> Decision:
        return suggest_next_step(
            rules, info, rx_by_week[week], by_week[week], units,
            by_week[prev] if prev is not None else None,
            rx_by_week[prev] if prev is not None else None,
        )

    weeks_logged = sorted(by_week)
    hard = [w for w in weeks_logged if w not in deload_weeks]
    last = weeks_logged[-1]
    in_progress = False

    if last in deload_weeks:
        rx = rx_by_week[last]
        step, reason, load, reps = "hold", "Deload week; resume progression next week.", rx.load, rx.reps
    elif not is_logged_in_full(by_week[last], rx_by_week[last]):
        # Session underway: show the target for it, decided from the last finished exposure.
        in_progress = True
        finished = [w for w in hard if w < last]
        if finished:
            prev = finished[-2] if len(finished) >= 2 else None
            step, reason, load, reps = decide(finished[-1], prev)
            reason = f"This session: {reason}"
        else:
            rx = rx_by_week[last]
            top = top_of_range(rules, rx.reps, rx.reps_max)
            step, load, reps = "hold", rx.load, rx.reps
            reason = (f"First session: work in {rx.reps}-{top} reps at RPE {rx.target_rpe:g}"
                      + (f" with {rx.load:g} {units}." if rx.load else "; pick a working weight."))
    else:
        prev = hard[-2] if len(hard) >= 2 else None
        step, reason, load, reps = decide(last, prev)

    return SlotMetrics(
        slot_id=key[1], day=key[0], exposures=exposures, plateau=plateau, regressing=regressing,
        in_progress=in_progress, next_step=step, next_step_reason=reason,
        suggested_load=load, suggested_reps=reps,
    )


def _volume_status(muscle: str, hard_sets: float, low: float, high: float, deload: bool) -> VolumeStatus:
    if muscle not in VOLUME_TARGET_MUSCLES:
        return "no_target"
    if deload:
        return "deload"
    if hard_sets < low:
        return "below"
    if hard_sets > high:
        return "above"
    return "in_range"


def compute_metrics(goal: Goal, program: Program, logs: list[SetLog]) -> Metrics:
    index = PrescriptionIndex(program)
    units = program.units
    deload_weeks = {w.week for w in program.weeks if w.is_deload}
    volume_rules = goal_rules(goal).volume

    logged_sessions: set[tuple[int, int]] = set()
    tonnage: dict[int, float] = defaultdict(float)
    hard_sets: dict[int, int] = defaultdict(int)
    unrated: dict[int, int] = defaultdict(int)
    week_gaps: dict[int, list[float]] = defaultdict(list)
    muscle_sets: dict[tuple[int, str], float] = defaultdict(float)
    muscle_sources: dict[tuple[int, str], dict[str, float]] = defaultdict(lambda: defaultdict(float))

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
            e1rm = set_e1rm(log.weight, log.reps, log.rpe, rx.target_rpe if rx else None)
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
                muscle_sources[(log.week, muscle)][lift] += credit

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

    muscle_volume = []
    for (w, m), v in sorted(muscle_sets.items()):
        targeted = m in VOLUME_TARGET_MUSCLES
        sources = sorted(muscle_sources[(w, m)].items(), key=lambda kv: (-kv[1], kv[0]))
        muscle_volume.append(MuscleVolume(
            week=w, muscle=m, hard_sets=v,
            contributors=[VolumeContribution(exercise_id=e, hard_sets=s) for e, s in sources],
            target_low=volume_rules.start.low if targeted else None,
            target_max=volume_rules.max if targeted else None,
            status=_volume_status(m, v, volume_rules.start.low, volume_rules.max, w in deload_weeks),
        ))

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
