"use client";

import { useState } from "react";
import { useApp } from "@/lib/store";
import type { PrescribedExercise, SetLog, SetType } from "@/lib/types";
import { exerciseName, isTimed, nextStepTag, prescriptionLine, warmupPlan } from "@/lib/view";
import { draftSet, findSlotMetrics, previousExposure, slotLogs, type SessionRef } from "@/lib/workout";
import SetRow, { type SetValues } from "./SetRow";
import { chip } from "./ui";

interface Props {
  ex: PrescribedExercise;
  sessionRef: SessionRef;
  isOpen: boolean;
  onToggle(): void;
}

const TAG_TONE = {
  weight: "border-orange bg-orange text-ink",
  reps: "border-orange bg-rubber-inset text-orange",
  hold: "border-steel bg-rubber-inset text-chalk-dim",
  reduce: "border-chalk bg-rubber-inset text-chalk",
} as const;

export default function ExerciseRow({ ex, sessionRef, isOpen, onToggle }: Props) {
  const program = useApp((s) => s.program)!;
  const logs = useApp((s) => s.logs);
  const metrics = useApp((s) => s.metrics);
  const catalog = useApp((s) => s.catalog);
  const rules = useApp((s) => s.rules);
  const upsertSet = useApp((s) => s.upsertSet);
  const removeSet = useApp((s) => s.removeSet);
  const [extra, setExtra] = useState<Record<SetType, number>>({ warmup: 0, working: 0, backoff: 0 });

  const units = program.units;
  const timed = isTimed(catalog, ex.exercise_id);
  const working = slotLogs(logs, program.block, sessionRef, ex.slot_id, ex.exercise_id, "working");
  const warmups = slotLogs(logs, program.block, sessionRef, ex.slot_id, ex.exercise_id, "warmup");
  const last = previousExposure(program, logs, sessionRef, ex.slot_id, ex.exercise_id);

  // The engine's decision applies to the session it was made for: the in-progress one,
  // or the next session after the slot's latest logged week.
  const slot = findSlotMetrics(metrics, sessionRef.day, ex.slot_id);
  const latestWeek = Math.max(0, ...logs.filter((l) => l.block === program.block && l.day === sessionRef.day &&
    l.slot_id === ex.slot_id && l.exercise_id === ex.exercise_id).map((l) => l.week));
  const decisionApplies = slot !== null &&
    (slot.in_progress ? sessionRef.week === latestWeek : sessionRef.week > latestWeek);
  const tag = decisionApplies ? nextStepTag(slot, units, timed) : null;

  const targetLoad = (decisionApplies ? slot?.suggested_load : null) ?? ex.load ?? last?.load ?? null;
  const targetReps = decisionApplies ? slot?.suggested_reps ?? null : null;

  // Suggested warm-up ramp for sessions still to do (not for finished sessions that skipped warm-ups).
  const sessionFinished = working.filter((l) => l.completed && l.reps > 0).length >= ex.sets;
  const warmupSuggestion = sessionFinished ? [] : warmupPlan(rules, catalog[ex.exercise_id], targetLoad, units);

  const rowsFor = (saved: SetLog[], type: SetType, prescribed: number) => {
    const count = Math.max(prescribed, ...saved.map((l) => l.set_number), 0) + extra[type];
    return Array.from({ length: count }, (_, i) => ({ n: i + 1, saved: saved.find((l) => l.set_number === i + 1) }));
  };

  const save = (type: SetType, n: number, saved: SetLog | undefined, v: SetValues) => {
    const base = saved ?? { ...draftSet(program, sessionRef, ex, n), set_type: type };
    upsertSet({ ...base, weight: v.weight, reps: v.reps, rpe: type === "warmup" ? null : v.rpe, completed: true });
  };

  const done = working.filter((l) => l.completed && l.reps > 0 && l.set_number <= ex.sets).length;

  return (
    <div className={`border-2 transition-colors ${isOpen ? "border-steel bg-tile" : "border-line bg-[#313131] hover:border-[#6a6a6a]"}`}>
      <div className="flex items-center">
        <button onClick={onToggle} aria-expanded={isOpen}
          className="flex min-h-11 min-w-0 flex-1 items-center gap-4 px-[18px] py-4 text-left">
          <svg className={`flex-none transition-transform duration-200 ${isOpen ? "rotate-90" : ""}`} width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#aba69c" strokeWidth="2.5" strokeLinecap="square" aria-hidden="true"><path d="M9 6l6 6-6 6" /></svg>
          <span className="flex min-w-0 flex-col gap-1">
            <span className="flex flex-wrap items-center gap-2.5">
              <span className="display text-[26px] font-extrabold leading-none">{exerciseName(catalog, ex.exercise_id)}</span>
              <span className="border border-[#555] px-1.5 py-0.5 text-[11px] font-extrabold tracking-[0.1em] text-muted uppercase">
                {catalog[ex.exercise_id]?.kind ?? "lift"}
              </span>
              {done >= ex.sets && <span className="text-xs font-bold uppercase tracking-[0.08em] text-orange">Done</span>}
            </span>
            <span className="text-sm text-muted tabular">
              {prescriptionLine(ex, units, catalog)}
              <span className="text-faint"> · </span>
              {last ? `last week ${last.load ? `${last.load} × ` : ""}${last.reps.join(" ")}${timed ? "s" : ""}` : "first time"}
            </span>
          </span>
        </button>
        {tag && slot && (
          <span tabIndex={0} className={`group relative mr-4 min-h-10 cursor-help whitespace-nowrap border-2 px-3 py-2 text-xs font-extrabold uppercase tracking-[0.08em] focus:outline-none ${TAG_TONE[tag.tone]}`}>
            {tag.label}
            <span role="tooltip" className="pointer-events-none absolute right-0 top-[calc(100%+8px)] z-20 w-[300px] -translate-y-1 whitespace-normal bg-chalk px-3.5 py-3 text-left text-[13px] font-medium normal-case tracking-normal text-ink opacity-0 shadow-[0_6px_0_var(--color-shadow)] transition-[opacity,transform] duration-150 group-hover:translate-y-0 group-hover:opacity-100 group-focus:translate-y-0 group-focus:opacity-100">
              {slot.next_step_reason}
            </span>
          </span>
        )}
      </div>

      {isOpen && (
        <div>
          <div className="grid grid-cols-[48px_130px_120px_minmax(0,1fr)_56px_56px] items-center gap-4 border-t border-line-soft px-[18px] pb-1.5 pt-2.5 text-[11px] font-extrabold uppercase tracking-[0.12em] text-muted">
            <span>Set</span><span>Weight ({units})</span><span>{timed ? "Seconds" : "Reps"}</span><span>Effort (RPE)</span><span /><span>Done</span>
          </div>
          {rowsFor(warmups, "warmup", warmupSuggestion.length).map(({ n, saved }) => {
            const plan = warmupSuggestion[n - 1] ?? warmupSuggestion[warmupSuggestion.length - 1];
            return (
              <SetRow key={`w${n}-${saved?.id ?? "draft"}`} label={`W${n}`} warmup saved={saved}
                defaults={{ weight: saved?.weight ?? plan?.weight ?? null, reps: saved?.reps ?? plan?.reps ?? null, rpe: null }}
                repsPlaceholder={plan ? String(plan.reps) : "5"}
                onSave={(v) => save("warmup", n, saved, v)} onUnsave={() => saved && removeSet(saved)} />
            );
          })}
          {rowsFor(working, "working", ex.sets).map(({ n, saved }) => (
            <SetRow key={`s${n}-${saved?.id ?? "draft"}`} label={String(n)} saved={saved}
              defaults={{ weight: saved?.weight ?? targetLoad, reps: saved?.reps ?? null, rpe: saved?.rpe ?? ex.target_rpe }}
              repsPlaceholder={targetReps ? String(targetReps) : `${ex.reps}${ex.reps_max ? `–${ex.reps_max}` : ""}`}
              targetRpe={ex.target_rpe} timed={timed}
              onSave={(v) => save("working", n, saved, v)} onUnsave={() => saved && removeSet(saved)} />
          ))}
          <div className="flex flex-wrap items-center gap-2.5 border-t border-line-soft px-[18px] pb-4 pt-3">
            <button className={chip()} onClick={() => setExtra((e) => ({ ...e, working: e.working + 1 }))}>+ Set</button>
            <button className={chip()} onClick={() => setExtra((e) => ({ ...e, warmup: e.warmup + 1 }))}>+ Warm-up</button>
            <span className="text-xs text-faint">
              Suggested warm-ups ramp to your working weight and never count toward progress. Tap ✓ to save a set.
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
