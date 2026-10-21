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

export type Goal = Profile["goal"];
export type Units = Profile["units"];
export type SetType = SetLog["set_type"];
export type NextStep = SlotMetrics["next_step"];
