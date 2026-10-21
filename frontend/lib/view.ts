/**
 * Display helpers: turn program/metrics data into labels for the UI.
 * Formatting and lookups only: every training number comes from the backend.
 */
import type { ExerciseInfo, Metrics, PrescribedExercise, Program, RulesResponse, SlotMetrics, Units } from "./types";

export type Catalog = Record<string, ExerciseInfo>;

export const exerciseName = (catalog: Catalog, id: string) =>
  catalog[id]?.name ?? id.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

export const muscleName = (m: string) => m.replace(/_/g, " ");

export const isTimed = (catalog: Catalog, id: string) => catalog[id]?.timed ?? false;

/** "185 lb", "BW", or null when the lifter still has to pick a weight. */
export function loadLabel(load: number | null | undefined, units: Units, catalog: Catalog, id: string): string | null {
  if (load === null || load === undefined) return null;
  if (load === 0 && catalog[id]?.equipment === "bodyweight") return "BW";
  return `${load % 1 === 0 ? load : load.toFixed(1)} ${units}`;
}

export function repRange(ex: PrescribedExercise, timed: boolean): string {
  const range = ex.reps_max && ex.reps_max !== ex.reps ? `${ex.reps}–${ex.reps_max}` : `${ex.reps}`;
  return timed ? `${range}s` : range;
}

/** "3 × 3–5 · 185 lb · RPE 8" */
export function prescriptionLine(ex: PrescribedExercise, units: Units, catalog: Catalog): string {
  const load = loadLabel(ex.load, units, catalog, ex.exercise_id) ?? "pick weight";
  return `${ex.sets} × ${repRange(ex, isTimed(catalog, ex.exercise_id))} · ${load} · RPE ${ex.target_rpe}`;
}

export type TagTone = "weight" | "reps" | "hold" | "reduce";

/** Short label + tone for a slot's next step. */
export function nextStepTag(slot: SlotMetrics, units: Units, timed: boolean): { label: string; tone: TagTone } {
  const load = slot.suggested_load;
  switch (slot.next_step) {
    case "add_load":
      return { label: load ? `Add weight · ${load} ${units}` : "Harder variation", tone: "weight" };
    case "add_reps":
      return { label: `Add reps · aim ${slot.suggested_reps}${timed ? "s" : ""}`, tone: "reps" };
    case "reduce":
      return { label: load ? `Reduce · ${load} ${units}` : "Easier variation", tone: "reduce" };
    default:
      return { label: load === null ? "Pick a weight" : "Hold", tone: "hold" };
  }
}

/** Sessions ("week-day") where any slot set a rep or load PR (first exposures don't count). */
export function prSessions(metrics: Metrics | null): Set<string> {
  const found = new Set<string>();
  for (const lift of metrics?.lifts ?? []) {
    for (const slot of lift.slots) {
      slot.exposures
        .filter((e) => !e.deload)
        .forEach((e, i) => {
          if (i > 0 && e.pr) found.add(`${e.week}-${slot.day}`);
        });
    }
  }
  return found;
}

/** Best e1RM per week for one lift. */
export function e1rmByWeek(metrics: Metrics | null, exerciseId: string): Map<number, number> {
  const out = new Map<number, number>();
  const lift = metrics?.lifts.find((l) => l.exercise_id === exerciseId);
  for (const p of lift?.e1rm_history ?? []) out.set(p.week, Math.max(out.get(p.week) ?? 0, p.e1rm));
  return out;
}

/** Main lifts that have e1RM data, for the strip and table. */
export function trackedMainLifts(metrics: Metrics | null) {
  return (metrics?.lifts ?? []).filter((l) => l.kind === "main" && l.e1rm_history.length > 0);
}

export interface DeloadInfo {
  headline: string;
  detail: string;
  urgent: boolean;
}

export function deloadInfo(program: Program, currentWeek: number, metrics: Metrics | null): DeloadInfo | null {
  const reactive = metrics?.deload.recommended && !metrics.deload.scheduled;
  if (reactive) {
    return { headline: "Deload recommended", detail: metrics!.deload.reasons.join(" "), urgent: true };
  }
  const deloadWeek = program.weeks.find((w) => w.is_deload)?.week;
  if (!deloadWeek) return null;
  const away = deloadWeek - currentWeek;
  const how = "Sets drop, effort targets drop, and you recover before the next block.";
  if (away < 0) return null;
  if (away === 0) return { headline: "Deload week", detail: `Lighter on purpose. ${how}`, urgent: true };
  return { headline: `Deload in ${away} week${away === 1 ? "" : "s"}`, detail: `Scheduled for week ${deloadWeek}. ${how}`, urgent: false };
}

export interface WarmupSet {
  weight: number;
  reps: number;
}

/**
 * Suggested warm-up ramp to `working` weight, using the backend's scheme (rules.shared.warmups):
 * each step's fraction of the working weight, rounded DOWN to a loadable weight, never below the
 * empty bar for barbell lifts, and dropping steps that don't climb. Empty for isolation lifts,
 * bodyweight/timed lifts, or when the working weight isn't known yet.
 */
export function warmupPlan(
  rules: RulesResponse | null, info: ExerciseInfo | undefined, working: number | null, units: Units,
): WarmupSet[] {
  if (!rules || !info || !working || info.timed || info.equipment === "bodyweight") return [];
  const step = rules.shared.min_load_step[units];
  const bar = info.equipment === "barbell" ? rules.shared.bar_weight[units] : step;
  const plan: WarmupSet[] = [];
  for (const s of rules.shared.warmups[info.kind] ?? []) {
    const weight = Math.max(bar, Math.floor((working * s.fraction) / step + 1e-9) * step);
    const last = plan[plan.length - 1];
    if (weight >= working || (last && weight <= last.weight)) continue;
    plan.push({ weight, reps: s.reps });
  }
  return plan;
}

/** The week the progress panel should show volume for: the latest finished week, else the latest with data. */
export function volumeWeek(program: Program, metrics: Metrics | null): number | null {
  const weeks = [...new Set((metrics?.muscle_volume ?? []).map((v) => v.week))].sort((a, b) => a - b);
  if (!weeks.length) return null;
  if (program.adjusted_through && weeks.includes(program.adjusted_through)) return program.adjusted_through;
  return weeks[weeks.length - 1];
}
