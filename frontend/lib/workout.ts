/**
 * Pure helpers over program + logs. No training math here: progression,
 * plateaus, volume etc. come from the backend's /metrics. These only answer
 * "where am I" and "what did I log" questions for the UI.
 *
 * Logged sets are identified by a stable `id`, so editing any field (reps, type,
 * set number) updates the same row instead of creating a new one.
 */
import type { Metrics, PrescribedExercise, Program, SetLog, SetType, SlotMetrics } from "./types";

export interface SessionRef {
  week: number;
  day: number;
}

export function newId(): string {
  return crypto.randomUUID();
}

/** Give every log an id (logs from the backend demo or older saves may not have one). */
export function withIds(logs: SetLog[]): SetLog[] {
  return logs.map((l) => (l.id ? l : { ...l, id: newId() }));
}

/** Fallback identity for a log without an id. */
function naturalKey(l: SetLog): string {
  return [l.block, l.week, l.day, l.slot_id ?? "-", l.exercise_id, l.set_type, l.set_number].join("|");
}

const sameRow = (a: SetLog, b: SetLog) => (a.id && b.id ? a.id === b.id : naturalKey(a) === naturalKey(b));

/** Insert or replace a set (same id), keeping logs in a stable order. */
export function upsertLog(logs: SetLog[], log: SetLog): SetLog[] {
  const i = logs.findIndex((l) => sameRow(l, log));
  if (i === -1) return [...logs, log];
  const next = logs.slice();
  next[i] = log;
  return next;
}

const sameGroup = (a: SetLog, b: SetLog) =>
  a.block === b.block && a.week === b.week && a.day === b.day && a.slot_id === b.slot_id &&
  a.exercise_id === b.exercise_id && a.set_type === b.set_type;

/** Remove a set, then renumber later sets of the same slot/exercise/type so numbering stays 1..n. */
export function removeLog(logs: SetLog[], log: SetLog): SetLog[] {
  const target = logs.find((l) => sameRow(l, log));
  if (!target) return logs;
  return logs
    .filter((l) => l !== target)
    .map((l) => (sameGroup(l, target) && l.set_number > target.set_number ? { ...l, set_number: l.set_number - 1 } : l));
}

export function slotLogs(
  logs: SetLog[], block: number, ref: SessionRef, slotId: string, exerciseId: string, setType?: SetType,
): SetLog[] {
  return logs
    .filter((l) => l.block === block && l.week === ref.week && l.day === ref.day && l.slot_id === slotId &&
      l.exercise_id === exerciseId && (setType === undefined || l.set_type === setType))
    .sort((a, b) => a.set_number - b.set_number);
}

export function nextSetNumber(
  logs: SetLog[], block: number, ref: SessionRef, slotId: string, exerciseId: string, setType: SetType,
): number {
  return slotLogs(logs, block, ref, slotId, exerciseId, setType).reduce((n, l) => Math.max(n, l.set_number), 0) + 1;
}

const isDone = (l: SetLog) => l.completed && l.reps > 0;

export function getSession(program: Program, ref: SessionRef) {
  return program.weeks.find((w) => w.week === ref.week)?.sessions.find((s) => s.day === ref.day) ?? null;
}

/** Completed prescribed working sets vs prescribed sets (same counting as the backend). */
export function sessionProgress(program: Program, logs: SetLog[], ref: SessionRef) {
  const session = getSession(program, ref);
  if (!session) return { done: 0, total: 0, exercisesDone: 0, exercises: 0 };
  let done = 0;
  let total = 0;
  let exercisesDone = 0;
  for (const ex of session.exercises) {
    const counted = slotLogs(logs, program.block, ref, ex.slot_id, ex.exercise_id, "working")
      .filter((l) => l.set_number <= ex.sets && isDone(l)).length;
    done += counted;
    total += ex.sets;
    if (counted >= ex.sets) exercisesDone++;
  }
  return { done, total, exercisesDone, exercises: session.exercises.length };
}

export type SessionStatus = "done" | "in_progress" | "planned";

const hasLogs = (program: Program, logs: SetLog[], ref: SessionRef) =>
  logs.some((l) => l.block === program.block && l.week === ref.week && l.day === ref.day);

export function sessionStatus(program: Program, logs: SetLog[], ref: SessionRef): SessionStatus {
  const { done, total } = sessionProgress(program, logs, ref);
  if (total > 0 && done >= total) return "done";
  return hasLogs(program, logs, ref) ? "in_progress" : "planned";
}

export function orderedSessions(program: Program): SessionRef[] {
  return program.weeks
    .slice()
    .sort((a, b) => a.week - b.week)
    .flatMap((w) => w.sessions.slice().sort((a, b) => a.day - b.day).map((s) => ({ week: w.week, day: s.day })));
}

/**
 * Where the lifter is. The latest session with any logs is the anchor: if it isn't
 * finished, that's the current session; otherwise the next session after it. Earlier
 * unfinished sessions count as skipped, so one missed accessory can't pin the app to week 1.
 */
export function currentPosition(program: Program, logs: SetLog[]): SessionRef {
  const refs = orderedSessions(program);
  let anchor = -1;
  refs.forEach((r, i) => {
    if (hasLogs(program, logs, r)) anchor = i;
  });
  if (anchor === -1) return refs[0];
  if (sessionStatus(program, logs, refs[anchor]) !== "done") return refs[anchor];
  return refs[anchor + 1] ?? refs[anchor];
}

export interface PreviousExposure {
  week: number;
  load: number;
  reps: number[];
}

/** What the lifter did on this slot (same exercise) the last time, for "last week: 185 x 5 5 4". */
export function previousExposure(
  program: Program, logs: SetLog[], ref: SessionRef, slotId: string, exerciseId: string,
): PreviousExposure | null {
  for (let week = ref.week - 1; week >= 1; week--) {
    const working = slotLogs(logs, program.block, { week, day: ref.day }, slotId, exerciseId, "working").filter(isDone);
    if (working.length) {
      const load = Math.max(...working.map((l) => l.weight));
      return { week, load, reps: working.filter((l) => l.weight === load).map((l) => l.reps) };
    }
  }
  return null;
}

export function findSlotMetrics(metrics: Metrics | null, day: number, slotId: string): SlotMetrics | null {
  for (const lift of metrics?.lifts ?? []) {
    const slot = lift.slots.find((s) => s.day === day && s.slot_id === slotId);
    if (slot) return slot;
  }
  return null;
}

/**
 * A blank working set pre-filled from the prescription (or last week's load). This is a UI
 * draft: keep it in component state and only save it (upsertSet) once the lifter marks it
 * done or failed. The backend reads a saved `completed: false` set as a FAILED set.
 */
export function draftSet(
  program: Program, ref: SessionRef, ex: PrescribedExercise, setNumber: number, lastLoad?: number,
): SetLog {
  return {
    id: newId(),
    block: program.block,
    week: ref.week,
    day: ref.day,
    slot_id: ex.slot_id,
    exercise_id: ex.exercise_id,
    set_type: "working",
    set_number: setNumber,
    weight: ex.load ?? lastLoad ?? 0,
    reps: 0,
    rpe: null,
    completed: false,
    note: "",
  };
}

/** Mirror of the backend's SetLog limits, so one bad row can't make every /metrics call fail. */
export function validateSetLog(l: SetLog): string | null {
  const int = (v: number, lo: number, hi: number) => Number.isInteger(v) && v >= lo && v <= hi;
  if (!l.exercise_id) return "missing exercise";
  if (!["warmup", "working", "backoff"].includes(l.set_type)) return `unknown set type "${l.set_type}"`;
  if (!int(l.block, 1, Number.MAX_SAFE_INTEGER)) return "block must be 1 or more";
  if (!int(l.week, 1, 16)) return "week must be 1-16";
  if (!int(l.day, 1, 7)) return "day must be 1-7";
  if (!int(l.set_number, 1, 20)) return "set number must be 1-20";
  if (!int(l.reps, 0, 300)) return "reps must be a whole number 0-300";
  if (!(typeof l.weight === "number" && Number.isFinite(l.weight) && l.weight >= 0)) return "weight must be 0 or more";
  if (l.rpe !== null && l.rpe !== undefined && !(l.rpe >= 1 && l.rpe <= 10)) return "RPE must be 1-10";
  return null;
}
