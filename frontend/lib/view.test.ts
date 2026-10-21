import { describe, expect, it } from "vitest";
import { benchSets, emptyMetrics, program } from "./test-fixtures";
import type { ExerciseInfo, Metrics, RulesResponse, SlotMetrics } from "./types";
import { deloadInfo, loadLabel, nextStepTag, prSessions, warmupPlan } from "./view";

const rules = {
  goals: {},
  shared: {
    min_load_step: { lb: 5, kg: 2.5 },
    bar_weight: { lb: 45, kg: 20 },
    warmups: {
      main: [{ fraction: 0.4, reps: 8 }, { fraction: 0.6, reps: 5 }, { fraction: 0.75, reps: 3 }, { fraction: 0.85, reps: 1 }],
      compound: [{ fraction: 0.5, reps: 8 }, { fraction: 0.75, reps: 4 }],
      isolation: [],
    },
  },
} as unknown as RulesResponse;

const info = (over: Partial<ExerciseInfo>): ExerciseInfo => ({
  id: "bench_press", name: "Barbell Bench Press", equipment: "barbell", kind: "main",
  primary: ["chest"], secondary: [], timed: false, ...over,
});

describe("warmupPlan", () => {
  it("ramps a main lift: 40/60/75/85%, rounded down to loadable weights", () => {
    expect(warmupPlan(rules, info({}), 225, "lb")).toEqual([
      { weight: 90, reps: 8 }, { weight: 135, reps: 5 }, { weight: 165, reps: 3 }, { weight: 190, reps: 1 },
    ]);
  });

  it("never goes below the empty bar and drops steps that don't climb", () => {
    // 65 lb: 40/60/75% all round to the 45 lb bar -> one bar set; 85% = 55
    expect(warmupPlan(rules, info({}), 65, "lb")).toEqual([{ weight: 45, reps: 8 }, { weight: 55, reps: 1 }]);
    // 95 lb: 40% -> bar, 60% = 57 -> 55, 75% -> 70, 85% -> 80
    expect(warmupPlan(rules, info({}), 95, "lb")).toEqual([
      { weight: 45, reps: 8 }, { weight: 55, reps: 5 }, { weight: 70, reps: 3 }, { weight: 80, reps: 1 },
    ]);
  });

  it("compounds get two steps; isolation, bodyweight and unknown weights get none", () => {
    expect(warmupPlan(rules, info({ kind: "compound", equipment: "machine" }), 300, "lb"))
      .toEqual([{ weight: 150, reps: 8 }, { weight: 225, reps: 4 }]);
    expect(warmupPlan(rules, info({ kind: "isolation", equipment: "dumbbell" }), 30, "lb")).toEqual([]);
    expect(warmupPlan(rules, info({ kind: "compound", equipment: "bodyweight" }), 0, "lb")).toEqual([]);
    expect(warmupPlan(rules, info({}), null, "lb")).toEqual([]);
    expect(warmupPlan(null, info({}), 225, "lb")).toEqual([]);
  });

  it("works in kg", () => {
    expect(warmupPlan(rules, info({}), 100, "kg")).toEqual([
      { weight: 40, reps: 8 }, { weight: 60, reps: 5 }, { weight: 75, reps: 3 }, { weight: 85, reps: 1 },
    ]);
  });
});

describe("labels", () => {
  it("loadLabel shows BW for bodyweight and null when no weight is known", () => {
    const catalog = { pull_up: info({ id: "pull_up", equipment: "bodyweight" }) };
    expect(loadLabel(0, "lb", catalog, "pull_up")).toBe("BW");
    expect(loadLabel(null, "lb", catalog, "pull_up")).toBeNull();
    expect(loadLabel(185, "lb", {}, "bench_press")).toBe("185 lb");
  });

  it("nextStepTag", () => {
    const slot: Omit<SlotMetrics, "next_step" | "suggested_load"> = {
      slot_id: "b", day: 1, exposures: [], plateau: false, regressing: false, in_progress: false,
      next_step_reason: "", suggested_reps: 6,
    };
    expect(nextStepTag({ ...slot, next_step: "add_load", suggested_load: 190 }, "lb", false))
      .toEqual({ label: "Add weight · 190 lb", tone: "weight" });
    expect(nextStepTag({ ...slot, next_step: "add_reps", suggested_load: 185 }, "lb", false).label).toBe("Add reps · aim 6");
    expect(nextStepTag({ ...slot, next_step: "hold", suggested_load: null }, "lb", false).label).toBe("Pick a weight");
  });
});

describe("prSessions", () => {
  it("marks PRs but never a lift's first exposure", () => {
    const m: Metrics = {
      ...emptyMetrics(),
      lifts: [{
        exercise_id: "bench_press", name: "Bench", style: "strength", kind: "main", e1rm_history: [], best_e1rm: 0,
        avg_rpe_gap: null, avg_rir: null, unrated_sets: 0, missed_reps: 0, plateau: false, plateau_suggestion: null,
        regressing: false,
        slots: [{
          slot_id: "bench", day: 1, plateau: false, regressing: false, in_progress: false, next_step: "add_reps",
          next_step_reason: "", suggested_load: 185, suggested_reps: 5,
          exposures: [
            { week: 1, load: 185, reps: [4], avg_reps: 4, avg_rpe: 8, pr: true, deload: false },
            { week: 2, load: 185, reps: [5], avg_reps: 5, avg_rpe: 8, pr: true, deload: false },
          ],
        }],
      }],
    };
    expect([...prSessions(m)]).toEqual(["2-1"]);
  });
});

describe("deloadInfo", () => {
  it("counts down to the scheduled deload week", () => {
    const withDeload = { ...program, weeks: [...program.weeks, { ...program.weeks[0], week: 3, is_deload: true }] };
    expect(deloadInfo(withDeload, 1, null)?.headline).toBe("Deload in 2 weeks");
    expect(deloadInfo(withDeload, 3, null)?.headline).toBe("Deload week");
  });

  it("a reactive recommendation takes priority", () => {
    const m = { ...emptyMetrics(), deload: { recommended: true, scheduled: false, next_week: 2, reasons: ["Effort 1+ RPE over target."] } };
    expect(deloadInfo(program, 1, m)).toMatchObject({ headline: "Deload recommended", urgent: true });
  });

  it("no deload week and no signal -> nothing to show", () => {
    expect(deloadInfo(program, 1, null)).toBeNull();
    void benchSets;
  });
});
