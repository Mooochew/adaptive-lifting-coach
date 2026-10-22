"use client";

import { useState } from "react";
import { appStore, useApp } from "@/lib/store";
import type { Profile } from "@/lib/types";
import { MAX_WEIGHT } from "@/lib/workout";
import { btn, btnPrimary, chip, field, label } from "./ui";

type Equipment = Profile["equipment"][number];

const GOALS: { value: Profile["goal"]; name: string; blurb: string }[] = [
  { value: "hypertrophy", name: "Hypertrophy", blurb: "Build muscle. Moderate reps, 10–20 hard sets per muscle." },
  { value: "strength", name: "Strength", blurb: "Lift heavier. Low reps on the main lifts, accessories for size." },
  { value: "general", name: "General", blurb: "Both: strength on main lifts, muscle-building everywhere else." },
];

const EQUIPMENT: { value: Equipment; name: string }[] = [
  { value: "barbell", name: "Barbell" },
  { value: "dumbbell", name: "Dumbbells" },
  { value: "machine", name: "Machines" },
  { value: "cable", name: "Cables" },
  { value: "kettlebell", name: "Kettlebells" },
];

const MAIN_LIFTS = [
  { id: "bench_press", name: "Bench press" },
  { id: "back_squat", name: "Back squat" },
  { id: "deadlift", name: "Deadlift" },
  { id: "overhead_press", name: "Overhead press" },
];

interface Props {
  onDone(): void;
  onCancel?: () => void;
}

/** Profile form -> rule-based starter program (the AI version comes in Phase 3). */
export default function Onboarding({ onDone, onCancel }: Props) {
  const existing = useApp((s) => s.profile);
  const busy = useApp((s) => s.busy);
  const startFromProfile = useApp((s) => s.startFromProfile);

  const [goal, setGoal] = useState<Profile["goal"]>(existing?.goal ?? "hypertrophy");
  const [experience, setExperience] = useState<Profile["experience"]>(existing?.experience ?? "intermediate");
  const [days, setDays] = useState(existing?.days_per_week ?? 4);
  const [minutes, setMinutes] = useState(existing?.session_minutes ?? 75);
  const [units, setUnits] = useState<Profile["units"]>(existing?.units ?? "lb");
  const [equipment, setEquipment] = useState<Equipment[]>(
    existing?.equipment.filter((e) => e !== "bodyweight") ?? EQUIPMENT.map((e) => e.value),   // default: full gym
  );
  const [injuries, setInjuries] = useState(existing?.injuries ?? "");
  const [background, setBackground] = useState(existing?.training_background ?? "");   // missing in older data
  const [lifts, setLifts] = useState<Record<string, { weight: string; reps: string }>>(() =>
    Object.fromEntries(MAIN_LIFTS.map((l) => {
      const e = existing?.lift_estimates.find((x) => x.exercise_id === l.id);
      return [l.id, { weight: e ? String(e.weight) : "", reps: e ? String(e.reps) : "" }];
    })),
  );

  const toggleEquipment = (e: Equipment) =>
    setEquipment((cur) => (cur.includes(e) ? cur.filter((x) => x !== e) : [...cur, e]));

  const fullGym = EQUIPMENT.every((e) => equipment.includes(e.value));
  const toggleFullGym = () => setEquipment(fullGym ? [] : EQUIPMENT.map((e) => e.value));

  const submit = async (ev: React.FormEvent) => {
    ev.preventDefault();
    const profile: Profile = {
      goal,
      experience,
      days_per_week: days,
      session_minutes: minutes,
      equipment: [...equipment, "bodyweight"],
      injuries: injuries.trim(),
      training_background: background.trim(),
      units,
      lift_estimates: MAIN_LIFTS.flatMap((l) => {
        const w = Number(lifts[l.id].weight);
        const r = Number(lifts[l.id].reps);
        return w > 0 && w <= MAX_WEIGHT && Number.isInteger(r) && r >= 1 && r <= 20
          ? [{ exercise_id: l.id, weight: w, reps: r }] : [];
      }),
    };
    await startFromProfile(profile);
    if (!appStore.getState().error) onDone();   // on failure, stay on the form; the banner explains
  };

  return (
    <form onSubmit={submit} className="animate-board-in flex max-w-4xl flex-col gap-9">
      <div>
        <p className="text-xs font-bold uppercase tracking-[0.16em] text-orange">New training block</p>
        <h1 className="display mt-1 text-5xl font-black leading-[0.95]">Build your program</h1>
        <p className="mt-3 text-muted">Takes a minute. You can change any of this later by starting a new block.</p>
      </div>

      <fieldset className="flex flex-col gap-3">
        <legend className={`${label} mb-3`}>Goal</legend>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          {GOALS.map((g) => (
            <label key={g.value}
              className={`flex cursor-pointer flex-col gap-1 border-2 p-4 transition-colors ${goal === g.value ? "border-orange bg-tile" : "border-line bg-tile-done hover:border-steel"}`}>
              <input type="radio" name="goal" value={g.value} checked={goal === g.value}
                onChange={() => setGoal(g.value)} className="sr-only" />
              <span className="display text-2xl font-extrabold">{g.name}</span>
              <span className="text-sm text-muted">{g.blurb}</span>
            </label>
          ))}
        </div>
      </fieldset>

      <div className="grid grid-cols-1 gap-6 md:grid-cols-4">
        <Select label="Experience" value={experience} onChange={(v) => setExperience(v as Profile["experience"])}
          options={[["beginner", "Beginner"], ["intermediate", "Intermediate"], ["advanced", "Advanced"]]} />
        <Select label="Days per week" value={String(days)} onChange={(v) => setDays(Number(v))}
          options={[2, 3, 4, 5, 6].map((d) => [String(d), `${d} days`])} />
        <Select label="Session length" value={String(minutes)} onChange={(v) => setMinutes(Number(v))}
          options={[30, 45, 60, 75, 90, 120].map((m) => [String(m), `${m} min`])} />
        <Select label="Units" value={units} onChange={(v) => setUnits(v as Profile["units"])}
          options={[["lb", "Pounds (lb)"], ["kg", "Kilograms (kg)"]]} />
      </div>

      <fieldset>
        <legend className={`${label} mb-3`}>Equipment you have (bodyweight is always included)</legend>
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" onClick={toggleFullGym} aria-pressed={fullGym} className={chip(fullGym)}>
            Full gym (everything)
          </button>
          <span className="mx-1 h-6 w-px bg-line" aria-hidden="true" />
          {EQUIPMENT.map((e) => (
            <button type="button" key={e.value} onClick={() => toggleEquipment(e.value)}
              aria-pressed={equipment.includes(e.value)}
              className={chip(equipment.includes(e.value))}>
              {e.name}
            </button>
          ))}
        </div>
      </fieldset>

      <fieldset>
        <legend className={`${label} mb-1`}>Recent sets on the main lifts (optional)</legend>
        <p className="mb-3 text-sm text-muted">
          A recent hard set sets your starting weights. Leave blank and you&apos;ll pick a working weight on day one.
        </p>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {MAIN_LIFTS.map((l) => (
            <div key={l.id} className="flex items-center gap-3 border-2 border-line-soft bg-tile-done px-4 py-3">
              <span className="flex-1 font-semibold">{l.name}</span>
              <input className={`${field} w-24`} type="number" min={0} max={MAX_WEIGHT} inputMode="decimal" placeholder={units}
                aria-label={`${l.name} weight in ${units}`} value={lifts[l.id].weight}
                onChange={(e) => setLifts((cur) => ({ ...cur, [l.id]: { ...cur[l.id], weight: e.target.value } }))} />
              <span className="text-muted">×</span>
              <input className={`${field} w-20`} type="number" min={1} max={20} inputMode="numeric" placeholder="reps"
                aria-label={`${l.name} reps`} value={lifts[l.id].reps}
                onChange={(e) => setLifts((cur) => ({ ...cur, [l.id]: { ...cur[l.id], reps: e.target.value } }))} />
            </div>
          ))}
        </div>
      </fieldset>

      <label className="flex flex-col gap-2">
        <span className={label}>Injuries or limitations (optional)</span>
        <textarea className={`${field} min-h-20 font-normal`} maxLength={500} value={injuries}
          onChange={(e) => setInjuries(e.target.value)} placeholder="e.g. left shoulder gets cranky on overhead pressing" />
      </label>

      <label className="flex flex-col gap-2">
        <span className={label}>Training background (optional)</span>
        <span className="text-sm text-muted">
          Your AI coach reads this when adjusting the plan: how long you&apos;ve trained, what has or hasn&apos;t
          worked, how you recover.
        </span>
        <textarea className={`${field} min-h-20 font-normal`} maxLength={500} value={background}
          onChange={(e) => setBackground(e.target.value)}
          placeholder="e.g. 6 years lifting; high volume leaves me beat up, I grow better on fewer, harder sets" />
      </label>

      <div className="flex flex-wrap items-center gap-3">
        <button type="submit" className={btnPrimary} disabled={busy !== null || equipment.length === 0}>
          {busy === "template" ? "Your coach is reviewing your program…" : "Build my program"}
        </button>
        {onCancel && <button type="button" className={btn()} onClick={onCancel}>Cancel</button>}
        {equipment.length === 0 && (
          <span className="text-sm text-muted" role="status">
            Pick at least one piece of equipment: bodyweight alone can&apos;t cover leg days yet.
          </span>
        )}
      </div>
    </form>
  );
}

function Select({ label: text, value, onChange, options }: {
  label: string; value: string; onChange(v: string): void; options: string[][];
}) {
  return (
    <label className="flex flex-col gap-2">
      <span className={label}>{text}</span>
      <select className={field} value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map(([v, name]) => <option key={v} value={v}>{name}</option>)}
      </select>
    </label>
  );
}
