"use client";

import { useState } from "react";
import type { SetLog } from "@/lib/types";
import { field } from "./ui";

export interface SetValues {
  weight: number;
  reps: number;
  rpe: number | null;
}

interface Props {
  label: string;
  saved?: SetLog;
  defaults: { weight: number | null; reps: number | null; rpe: number | null };
  repsPlaceholder: string;
  targetRpe?: number;
  warmup?: boolean;
  timed?: boolean;
  onSave(v: SetValues): void;
  onUnsave(): void;
}

/**
 * One set. Unsaved rows are drafts held here; ✓ saves them. Editing a saved row saves
 * each valid change; un-ticking removes it. The store never sees a half-typed row.
 */
export default function SetRow({ label, saved, defaults, repsPlaceholder, targetRpe, warmup, timed, onSave, onUnsave }: Props) {
  const [weight, setWeight] = useState(defaults.weight === null ? "" : String(defaults.weight));
  const [reps, setReps] = useState(defaults.reps === null ? "" : String(defaults.reps));
  const [rpe, setRpe] = useState<number | null>(defaults.rpe);
  const done = Boolean(saved?.completed && saved.reps > 0);

  const parse = (w: string, r: string, e: number | null): SetValues | null => {
    const wn = w === "" ? 0 : Number(w);
    const rn = Number(r);
    if (!Number.isFinite(wn) || wn < 0 || !Number.isInteger(rn) || rn < 1 || rn > 300) return null;
    return { weight: wn, reps: rn, rpe: e };
  };

  // Saved rows: persist each valid edit right away.
  const edit = (w: string, r: string, e: number | null) => {
    if (!done) return;
    const values = parse(w, r, e);
    if (values) onSave(values);
  };

  const toggle = () => {
    if (done) return onUnsave();
    const values = parse(weight, reps, rpe);
    if (values) onSave(values);
  };

  const ready = parse(weight, reps, rpe) !== null;
  const over = !warmup && targetRpe !== undefined && rpe !== null && rpe > targetRpe + 0.5;

  return (
    <div className={`grid grid-cols-[48px_130px_120px_minmax(0,1fr)_56px_56px] items-center gap-4 border-t border-line-soft px-[18px] py-2 ${done ? "bg-tile-done" : ""}`}>
      <span className={`display text-2xl font-extrabold tabular ${warmup ? "text-faint" : "text-muted"}`}>{label}</span>
      <input className={field} type="number" min={0} step="any" inputMode="decimal" aria-label={`Set ${label} weight`}
        placeholder="weight" value={weight}
        onChange={(e) => { setWeight(e.target.value); edit(e.target.value, reps, rpe); }} />
      <input className={`${field} text-xl`} type="number" min={1} max={300} inputMode="numeric"
        aria-label={`Set ${label} ${timed ? "seconds" : "reps"}`} placeholder={repsPlaceholder} value={reps}
        onChange={(e) => { setReps(e.target.value); edit(weight, e.target.value, rpe); }} />
      {warmup ? (
        <span className="text-sm text-faint">warm-up</span>
      ) : (
        <input type="range" min={6} max={10} step={0.5} value={rpe ?? targetRpe ?? 8} aria-label={`Set ${label} RPE`}
          className="min-h-8 w-full cursor-pointer accent-orange"
          onChange={(e) => { const v = Number(e.target.value); setRpe(v); edit(weight, reps, v); }} />
      )}
      <span className={`display text-right text-[22px] font-extrabold tabular ${over ? "text-orange" : "text-chalk"}`}>
        {warmup ? "" : rpe ?? "–"}
      </span>
      <button onClick={toggle} disabled={!done && !ready} aria-pressed={done}
        aria-label={done ? `Mark set ${label} not done` : `Save set ${label} as done`}
        className={`flex h-11 w-12 items-center justify-center border-2 transition-all duration-150 disabled:cursor-not-allowed disabled:opacity-40 ${
          done ? "animate-stamp border-orange bg-orange text-ink" : "border-[#555] bg-transparent text-chalk hover:border-orange"}`}>
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="square" aria-hidden="true"><path d="M5 12l5 5 9-10" /></svg>
      </button>
    </div>
  );
}
