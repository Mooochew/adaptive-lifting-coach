"""Carry the engine's decisions forward into the program (no LLM).

When a week ends, each slot's suggested load is written into the following
weeks' prescriptions, so the plan always matches the advice. A week has ended
when every session in it is fully logged, or when anything in a later week has
been logged (the lifter moved on).

Rules:
  - only weeks after the ended week change, and never a slot that already has logs
    in that week (what was prescribed when you lifted stays as it was)
  - deload weeks get the suggested load minus the goal's deload load cut
  - every changed week goes back through the guards, with the engine's decisions,
    so double progression is enforced here exactly as it will be for the AI
  - idempotent: `program.adjusted_through` records the last week applied
"""

from collections import defaultdict

from app.guards import guard_week
from app.rules import Goal, goal_rules
from app.schemas import Program, SetLog, SlotMetrics, Week
from app.training import PrescriptionIndex, compute_metrics, floor_to_increment, is_done, is_logged_in_full

SlotWeek = tuple[int, int, str]     # (week, day, slot_id)


def _logs_by_slot(program: Program, logs: list[SetLog]) -> dict[SlotWeek, list[SetLog]]:
    """This block's logs grouped by the prescribed slot they belong to (same resolver as the metrics)."""
    index = PrescriptionIndex(program)
    grouped: dict[SlotWeek, list[SetLog]] = defaultdict(list)
    for log in logs:
        if log.block != program.block:
            continue
        resolved = index.resolve(log)
        if resolved:
            grouped[(log.week, log.day, resolved[0])].append(log)
    return grouped


def last_ended_week(program: Program, logs: list[SetLog]) -> int:
    """Highest week that is fully logged or that the lifter has moved past (0 = none)."""
    by_slot = _logs_by_slot(program, logs)
    latest_logged = max((l.week for l in logs if l.block == program.block), default=0)
    ended = 0
    for week in sorted(program.weeks, key=lambda w: w.week):
        moved_on = latest_logged > week.week
        all_done = all(
            is_logged_in_full(by_slot.get((week.week, s.day, ex.slot_id), []), ex)
            for s in week.sessions for ex in s.exercises
        )
        if moved_on or all_done:
            ended = week.week
        else:
            break
    return ended


def _as_lifted(week: Week, by_slot: dict[SlotWeek, list[SetLog]]) -> Week:
    """The week's prescription with each slot's load raised to the heaviest working set actually lifted."""
    lifted = week.model_copy(deep=True)
    for session in lifted.sessions:
        for ex in session.exercises:
            weights = [l.weight for l in by_slot.get((week.week, session.day, ex.slot_id), [])
                       if l.set_type == "working" and is_done(l)]
            if weights:
                ex.load = max(max(weights), ex.load or 0.0)
    return lifted


def last_logged_loads(program: Program, logs: list[SetLog]) -> dict[str, float]:
    """Heaviest working weight from the most recent week each lift was done (this block)."""
    latest: dict[str, tuple[int, float]] = {}
    for log in logs:
        if log.block != program.block or log.set_type != "working" or not is_done(log) or log.weight <= 0:
            continue
        week, weight = latest.get(log.exercise_id, (0, 0.0))
        if log.week > week or (log.week == week and log.weight > weight):
            latest[log.exercise_id] = (log.week, log.weight)
    return {exercise_id: weight for exercise_id, (_, weight) in latest.items()}


def advance_program(goal: Goal, program: Program, logs: list[SetLog]) -> tuple[Program, list[str], list[str]]:
    """Apply suggestions from ended weeks to later weeks. Returns (program, changes, guard notes)."""
    through = last_ended_week(program, logs)
    if through <= program.adjusted_through:
        return program, [], []

    metrics = compute_metrics(goal, program, logs)
    decisions: dict[tuple[int, str], tuple[str, SlotMetrics]] = {
        (slot.day, slot.slot_id): (lift.exercise_id, slot) for lift in metrics.lifts for slot in lift.slots
    }
    load_cut = goal_rules(goal).deload.load_cut
    by_slot = _logs_by_slot(program, logs)
    logged = set(by_slot)
    last_loads = last_logged_loads(program, logs)

    updated = program.model_copy(deep=True)
    changes: list[str] = []
    notes: list[str] = []
    ended_week = next((w for w in updated.weeks if w.week == through), None)
    previous = _as_lifted(ended_week, by_slot) if ended_week else None

    for week in sorted(updated.weeks, key=lambda w: w.week):
        if week.week <= through:
            continue
        # Loads already in the plan were checked when it was made (e.g. a lift new this week).
        approved = {(s.day, ex.slot_id): ex.load for s in week.sessions for ex in s.exercises
                    if ex.load is not None}
        suggested_from: dict[tuple[int, str], float | None] = {}     # slot -> load before the suggestion
        for session in week.sessions:
            for ex in session.exercises:
                found = decisions.get((session.day, ex.slot_id))
                if not found or found[0] != ex.exercise_id or (week.week, session.day, ex.slot_id) in logged:
                    continue
                suggested = found[1].suggested_load
                if suggested is None:
                    continue
                new_load = (floor_to_increment(suggested * (1 - load_cut), updated.units)
                            if week.is_deload and suggested and load_cut else suggested)
                if new_load != ex.load:
                    suggested_from[(session.day, ex.slot_id)] = ex.load
                    ex.load = new_load

        result = guard_week(week, goal, updated.units, previous=previous, last_loads=last_loads,
                            approved_loads=approved, slot_metrics={k: v[1] for k, v in decisions.items()})
        notes += [f"W{week.week}: {n}" for n in result.notes + result.violations]
        week.sessions = result.week.sessions

        # Report what the lifter will actually see: the load after the guards, not the suggestion.
        if week.week == through + 1:
            for session in week.sessions:
                for ex in session.exercises:
                    key = (session.day, ex.slot_id)
                    if key in suggested_from and ex.load != suggested_from[key]:
                        old, new = suggested_from[key], ex.load
                        changes.append(f"W{week.week} D{session.day} {ex.slot_id}: "
                                       f"{'?' if old is None else f'{old:g}'} -> "
                                       f"{'?' if new is None else f'{new:g}'} {updated.units}")
        if not week.is_deload:          # loads returning after a deload aren't a "jump"
            previous = week

    updated.adjusted_through = through
    return updated, changes, notes
