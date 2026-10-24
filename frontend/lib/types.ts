/**
 * Friendly names for the API types. The source of truth is the Python
 * Pydantic models; api-types.ts is generated from them (npm run gen:types).
 */
import type { components } from "./api-types";

type Schemas = components["schemas"];

export type Profile = Schemas["Profile"];
export type LiftEstimate = Schemas["LiftEstimate"];
export type Program = Schemas["Program"];
export type Week = Schemas["Week"];
export type Session = Schemas["Session"];
export type PrescribedExercise = Schemas["PrescribedExercise"];
export type SetLog = Schemas["SetLog"];
export type Metrics = Schemas["Metrics"];
export type LiftMetrics = Schemas["LiftMetrics"];
export type SlotMetrics = Schemas["SlotMetrics"];
export type MuscleVolume = Schemas["MuscleVolume"];
export type DeloadSignal = Schemas["DeloadSignal"];
export type ExerciseInfo = Schemas["ExerciseInfo"];
export type RulesResponse = Schemas["RulesResponse"];
export type TemplateResponse = Schemas["TemplateResponse"];
export type DemoAthlete = Schemas["DemoAthlete"];
export type MetricsRequest = Schemas["MetricsRequest"];
export type Health = Schemas["Health"];
export type TemplateRequest = Schemas["TemplateRequest"];
export type AdvanceResponse = Schemas["AdvanceResponse"];
export type ReviewResponse = Schemas["ReviewResponse"];
export type AppliedEdit = Schemas["AppliedEdit"];
export type CoachNotesRequest = Schemas["CoachNotesRequest"];
export type CoachNotesResponse = Schemas["CoachNotesResponse"];

/** The AI coach's review of a program, kept with it (null for the demo or older data). */
export interface CoachReview {
  source: ReviewResponse["source"];
  summary: string;
  edits: AppliedEdit[];
  fallbackReason: string | null;
}

/** The AI coach's note on a week, kept with the program week it was written after. */
export interface StoredCoachNotes extends CoachNotesResponse {
  block: number;
  /** The program's adjusted_through when the note was asked for: a newer finished week asks again. */
  adjustedThrough: number;
}

export type Goal = Profile["goal"];
export type Units = Profile["units"];
export type SetType = SetLog["set_type"];
export type NextStep = SlotMetrics["next_step"];
