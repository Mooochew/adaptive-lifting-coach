"use client";

import { useState } from "react";
import { useApp } from "@/lib/store";
import type { Week } from "@/lib/types";
import { e1rmByWeek, exerciseName, trackedMainLifts } from "@/lib/view";
import { chip } from "./ui";

/** Estimated 1RM per week for the main lifts, aligned under the board's columns. */
export default function E1rmStrip({ weeks, currentWeek }: { weeks: Week[]; currentWeek: number }) {
  const metrics = useApp((s) => s.metrics);
  const catalog = useApp((s) => s.catalog);
  const units = useApp((s) => s.program?.units ?? "lb");
  const lifts = trackedMainLifts(metrics);
  const [picked, setPicked] = useState<string | null>(null);

  if (!lifts.length) return null;
  const active = lifts.find((l) => l.exercise_id === picked) ?? lifts[0];
  const byWeek = e1rmByWeek(metrics, active.exercise_id);
  const values = [...byWeek.values()];
  const max = Math.max(...values);
  const min = Math.min(...values);

  return (
    <section className="mt-7 border-t-2 border-line pt-[18px]" aria-label="Estimated one-rep max by week">
      <div className="mb-3.5 flex flex-wrap items-center gap-2.5">
        <span className="display mr-2 text-[22px] font-extrabold">Estimated max</span>
        {lifts.map((l) => (
          <button key={l.exercise_id} onClick={() => setPicked(l.exercise_id)} aria-pressed={l === active}
            className={chip(l === active)}>
            {exerciseName(catalog, l.exercise_id).replace(/^(Barbell|Standing|Conventional) /, "")}
          </button>
        ))}
        <span className="ml-auto text-xs text-muted">RPE-adjusted: 5 reps at RPE 8 counts as a 7-rep max</span>
      </div>
      <div className="overflow-x-auto">
        <div className="grid min-w-[900px] items-end gap-3" style={{ gridTemplateColumns: `repeat(${weeks.length}, minmax(0, 1fr))` }}>
          {weeks.map((w, i) => {
            const value = byWeek.get(w.week);
            const prev = i > 0 ? byWeek.get(weeks[i - 1].week) : undefined;
            const delta = value !== undefined && prev !== undefined ? Math.round(value - prev) : null;
            const height = value === undefined ? 0 : 30 + (max === min ? 70 : Math.round((70 * (value - min)) / (max - min)));
            return (
              <div key={w.week} className="flex flex-col gap-1.5 px-3">
                <div className="flex items-baseline gap-2">
                  <span className="display text-3xl font-black tabular">{value !== undefined ? Math.round(value) : "—"}</span>
                  <span className={`text-[13px] font-bold tabular ${delta !== null && delta > 0 ? "text-orange" : "text-faint"}`}>
                    {delta !== null ? `${delta >= 0 ? "+" : ""}${delta}` : w.is_deload ? "deload" : value === undefined ? "upcoming" : units}
                  </span>
                </div>
                <div className="flex h-16 items-end border border-line-soft bg-rubber-inset">
                  <div className={`w-full transition-[height] duration-500 ease-[var(--ease-snap)] ${w.week === currentWeek ? "bg-orange" : "bg-[#5a5a5a]"}`}
                    style={{ height: `${height}%` }} />
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}
