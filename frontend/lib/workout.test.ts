import { describe, expect, it } from "vitest";
import { benchSets, lateralSets, program, set, squatSets } from "./test-fixtures";
import {
  currentPosition,
  draftSet,
  nextSetNumber,
  previousExposure,
  removeLog,
  sessionProgress,
  sessionStatus,
  upsertLog,
  validateSetLog,
  withIds,
} from "./workout";

describe("upsertLog / removeLog", () => {
  it("replaces a set with the same id instead of duplicating it", () => {
    const logs = benchSets(1, [5, 5, 4]);
    const edited = upsertLog(logs, { ...logs[2], reps: 5 });
    expect(edited).toHaveLength(3);
    expect(edited[2].reps).toBe(5);
  });

  it("changing a set's type edits the same row (reviewer m6)", () => {
    const logs = benchSets(1, [5, 5, 4]);
    const edited = upsertLog(logs, { ...logs[0], set_type: "warmup" });
    expect(edited).toHaveLength(3);
    expect(edited[0].set_type).toBe("warmup");
  });

  it("keeps warm-ups and working sets with the same number separate", () => {
    const warmup = set({ week: 1, day: 1, slot_id: "bench", exercise_id: "bench_press", set_number: 1, set_type: "warmup" });
    expect(upsertLog(benchSets(1, [5]), warmup)).toHaveLength(2);
  });

  it("renumbers later sets after a removal and keeps their ids", () => {
    const logs = benchSets(1, [5, 4, 3]);
    const after = removeLog(logs, logs[0]);
    expect(after.map((l) => [l.set_number, l.reps])).toEqual([[1, 4], [2, 3]]);
    expect(after.map((l) => l.id)).toEqual([logs[1].id, logs[2].id]);
  });

  it("assigns ids to logs that don't have one", () => {
    const logs = withIds(benchSets(1, [5, 5]).map((l) => ({ ...l, id: null })));
    expect(logs.every((l) => l.id)).toBe(true);
    expect(new Set(logs.map((l) => l.id)).size).toBe(2);
  });

  it("next set number continues the sequence per type", () => {
    const logs = benchSets(1, [5, 5]);
    expect(nextSetNumber(logs, 1, { week: 1, day: 1 }, "bench", "bench_press", "working")).toBe(3);
    expect(nextSetNumber(logs, 1, { week: 1, day: 1 }, "bench", "bench_press", "warmup")).toBe(1);
  });
});

describe("session progress and status", () => {
  it("counts completed working sets against the prescription", () => {
    const p = sessionProgress(program, benchSets(1, [5, 5]), { week: 1, day: 1 });
    expect(p).toEqual({ done: 2, total: 6, exercisesDone: 0, exercises: 2 });
  });

  it("ignores warm-ups, incomplete sets and sets beyond the prescription (same as backend)", () => {
    const logs = [
      ...benchSets(1, [5, 5, 5, 5]),                                   // 4th set is extra
      set({ week: 1, day: 1, slot_id: "bench", exercise_id: "bench_press", set_number: 1, set_type: "warmup" }),
      set({ week: 1, day: 1, slot_id: "lateral", exercise_id: "lateral_raise", set_number: 1, completed: false }),
    ];
    expect(sessionProgress(program, logs, { week: 1, day: 1 }).done).toBe(3);
  });

  it("a failed set 1 plus extra sets doesn't count as done", () => {
    const logs = benchSets(1, [0, 5, 5, 5]).map((l, i) => (i === 0 ? { ...l, completed: false } : l));
    expect(sessionProgress(program, logs, { week: 1, day: 1 }).done).toBe(2);
  });

  it("planned / in progress / done", () => {
    const ref = { week: 1, day: 2 };
    expect(sessionStatus(program, [], ref)).toBe("planned");
    expect(sessionStatus(program, squatSets(1, 1), ref)).toBe("in_progress");
    expect(sessionStatus(program, squatSets(1, 3), ref)).toBe("done");
  });
});

describe("currentPosition", () => {
  it("starts at week 1 day 1", () => {
    expect(currentPosition(program, [])).toEqual({ week: 1, day: 1 });
  });

  it("stays on an in-progress session", () => {
    expect(currentPosition(program, benchSets(1, [5]))).toEqual({ week: 1, day: 1 });
  });

  it("moves past finished sessions", () => {
    const logs = [...benchSets(1, [5, 5, 5]), ...lateralSets(1)];
    expect(currentPosition(program, logs)).toEqual({ week: 1, day: 2 });
  });

  it("a skipped accessory doesn't pin the lifter to week 1 (reviewer M4)", () => {
    // W1D1: bench done, lateral raises skipped; then W1D2 done and W2D1 bench started
    const logs = [...benchSets(1, [5, 5, 5]), ...squatSets(1), ...benchSets(2, [5])];
    expect(currentPosition(program, logs)).toEqual({ week: 2, day: 1 });
  });

  it("a whole skipped session doesn't pin the lifter either", () => {
    const logs = [...benchSets(1, [5, 5, 5]), ...lateralSets(1), ...benchSets(2, [5, 5, 5]), ...lateralSets(2)];
    expect(currentPosition(program, logs)).toEqual({ week: 2, day: 2 });   // W1D2 skipped
  });

  it("finishing the last session stays on it", () => {
    const logs = [...benchSets(2, [5, 5, 5]), ...lateralSets(2), ...squatSets(2)];
    expect(currentPosition(program, logs)).toEqual({ week: 2, day: 2 });
  });
});

describe("previousExposure", () => {
  it("returns last week's working load and reps for the same slot", () => {
    const logs = [...benchSets(1, [5, 5, 4]), ...benchSets(2, [5])];
    expect(previousExposure(program, logs, { week: 2, day: 1 }, "bench", "bench_press"))
      .toEqual({ week: 1, load: 185, reps: [5, 5, 4] });
  });

  it("ignores a different exercise in the same slot after a swap (reviewer m7)", () => {
    const logs = benchSets(1, [5, 5, 4]);
    expect(previousExposure(program, logs, { week: 2, day: 1 }, "bench", "paused_bench")).toBeNull();
  });

  it("is null in week 1", () => {
    expect(previousExposure(program, benchSets(1, [5]), { week: 1, day: 1 }, "bench", "bench_press")).toBeNull();
  });
});

describe("draftSet", () => {
  it("pre-fills the prescribed load, falling back to last week's load, with a fresh id", () => {
    const [bench, lateral] = program.weeks[0].sessions[0].exercises;
    const draft = draftSet(program, { week: 1, day: 1 }, bench, 1);
    expect(draft.weight).toBe(185);
    expect(draft.id).toBeTruthy();
    expect(draftSet(program, { week: 2, day: 1 }, lateral, 1, 20).weight).toBe(20);
    expect(draftSet(program, { week: 1, day: 1 }, lateral, 1).weight).toBe(0);
  });
});

describe("validateSetLog", () => {
  const ok = benchSets(1, [5])[0];
  it.each([
    [{ ...ok, rpe: 0 }, "RPE must be 1-10"],
    [{ ...ok, set_number: 21 }, "set number must be 1-20"],
    [{ ...ok, reps: 4.5 }, "reps must be a whole number 0-300"],
    [{ ...ok, weight: -5 }, "weight must be 0 or more"],
    [{ ...ok, week: 17 }, "week must be 1-16"],
  ])("rejects out-of-range values like the backend", (log, msg) => {
    expect(validateSetLog(log)).toBe(msg);
  });

  it("accepts a normal set and an unrated set", () => {
    expect(validateSetLog(ok)).toBeNull();
    expect(validateSetLog({ ...ok, rpe: null })).toBeNull();
  });
});
