import type { Metrics, PrescribedExercise, Profile, Program, SetLog } from "./types";

export const profile: Profile = {
  goal: "general",
  experience: "intermediate",
  days_per_week: 2,
  session_minutes: 60,
  equipment: ["barbell", "dumbbell"],
  injuries: "",
  training_background: "",
  units: "lb",
  lift_estimates: [],
};

const rx = (slot_id: string, exercise_id: string, load: number | null, reps = 3, reps_max = 5): PrescribedExercise => ({
  slot_id, exercise_id, sets: 3, reps, reps_max, load, target_rpe: 8, rest_sec: 120, notes: "",
});

/** 2 weeks x 2 days: bench + lateral raise on day 1, squat on day 2. */
export function makeProgram(block = 1): Program {
  return {
    block,
    units: "lb",
    adjusted_through: 0,
    weeks: [1, 2].map((week) => ({
      week,
      is_deload: false,
      sessions: [
        { day: 1, focus: "Upper", exercises: [rx("bench", "bench_press", 185), rx("lateral", "lateral_raise", null, 10, 15)] },
        { day: 2, focus: "Lower", exercises: [rx("squat", "back_squat", 275)] },
      ],
    })),
  };
}

export const program = makeProgram();

let counter = 0;

export function set(over: Partial<SetLog> & Pick<SetLog, "week" | "day" | "slot_id" | "exercise_id" | "set_number">): SetLog {
  return {
    id: `t${++counter}`, block: 1, set_type: "working", weight: 185, reps: 5, rpe: 8, completed: true, note: "", ...over,
  };
}

export const benchSets = (week: number, reps: number[], weight = 185): SetLog[] =>
  reps.map((r, i) => set({ week, day: 1, slot_id: "bench", exercise_id: "bench_press", set_number: i + 1, reps: r, weight }));

export const lateralSets = (week: number, n = 3): SetLog[] =>
  Array.from({ length: n }, (_, i) =>
    set({ week, day: 1, slot_id: "lateral", exercise_id: "lateral_raise", set_number: i + 1, weight: 20, reps: 12 }));

export const squatSets = (week: number, n = 3): SetLog[] =>
  Array.from({ length: n }, (_, i) =>
    set({ week, day: 2, slot_id: "squat", exercise_id: "back_squat", set_number: i + 1, weight: 275 }));

export const emptyMetrics = (): Metrics => ({
  goal: "general",
  block: 1,
  lifts: [],
  weekly_volume: [],
  muscle_volume: [],
  sessions_logged: 0,
  sessions_skipped: [],
  deload: { recommended: false, scheduled: false, next_week: 1, reasons: [] },
});
