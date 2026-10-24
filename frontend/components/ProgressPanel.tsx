"use client";

import { useEffect } from "react";
import { useApp } from "@/lib/store";
import { e1rmByWeek, exerciseName, muscleName, trackedMainLifts, volumeWeek } from "@/lib/view";
import CoachNotes from "./CoachNotes";
import { btn } from "./ui";

const SCALE_MAX = 24; // bar scale in weekly sets

/** Slide-over with the AI coach's notes, volume per muscle, main-lift e1RM, plan changes and deload status. */
export default function ProgressPanel({ open, onClose }: { open: boolean; onClose(): void }) {
  const program = useApp((s) => s.program)!;
  const metrics = useApp((s) => s.metrics);
  const catalog = useApp((s) => s.catalog);
  const lastChanges = useApp((s) => s.lastChanges);
  const shortfalls = useApp((s) => s.volumeShortfalls);
  const status = useApp((s) => s.metricsStatus);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const week = volumeWeek(program, metrics);
  const muscles = (metrics?.muscle_volume ?? [])
    .filter((v) => v.week === week)
    .sort((a, b) => Number(a.status === "no_target") - Number(b.status === "no_target") || b.hard_sets - a.hard_sets);
  const lifts = trackedMainLifts(metrics);
  const tableWeeks = program.weeks.filter((w) => !w.is_deload).map((w) => w.week);
  const deload = metrics?.deload;

  return (
    <>
      <button aria-label="Close progress panel" tabIndex={-1} onClick={onClose}
        className={`fixed inset-0 z-30 bg-[rgba(20,20,20,0.55)] transition-opacity duration-300 ${open ? "opacity-100" : "pointer-events-none opacity-0"}`} />
      <aside aria-label="Progress" aria-hidden={!open} inert={!open}
        className={`fixed bottom-0 right-0 top-0 z-40 w-[440px] max-w-full overflow-y-auto border-l-2 border-line bg-rubber-deep transition-transform duration-300 ease-[var(--ease-snap)] ${open ? "translate-x-0" : "translate-x-[102%]"}`}>
        <div className="flex flex-col gap-7 px-7 pb-10 pt-7">
          <div className="flex items-center justify-between">
            <h2 className="display text-4xl font-black">Progress</h2>
            <button className={btn()} onClick={onClose} aria-label="Close progress panel">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="square" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
            </button>
          </div>

          {status === "loading" && !metrics && <p className="text-muted">Crunching your numbers…</p>}

          <CoachNotes />

          {deload?.recommended && (
            <section className="border-2 border-orange p-4">
              <h3 className="display text-xl font-extrabold text-orange">{deload.scheduled ? "Deload coming up" : "Deload recommended"}</h3>
              <ul className="mt-1.5 list-disc pl-5 text-sm text-muted">
                {deload.reasons.map((r) => <li key={r}>{r}</li>)}
              </ul>
            </section>
          )}

          {lastChanges.length > 0 && (
            <section>
              <h3 className="display text-[22px] font-extrabold">Plan updated</h3>
              <p className="mb-2 text-xs text-muted">Last week&apos;s results were applied to the weeks ahead:</p>
              <ul className="flex flex-col gap-1 text-sm tabular">
                {lastChanges.map((c) => <li key={c} className="border-l-2 border-orange pl-2">{c}</li>)}
              </ul>
            </section>
          )}

          <section>
            <div className="mb-1.5 flex items-baseline justify-between">
              <h3 className="display text-[22px] font-extrabold">Hard sets{week ? ` · week ${week}` : ""}</h3>
              {muscles[0]?.target_low != null && (
                <span className="text-[11px] font-bold uppercase tracking-[0.1em] text-muted">
                  Target {muscles[0].target_low}–{muscles[0].target_max}
                </span>
              )}
            </div>
            <p className="mb-2 text-xs text-muted">Hover a muscle to see which lifts counted. Secondary muscles get half a set.</p>
            {!muscles.length && <p className="text-sm text-faint">Log a few sets to see your weekly volume.</p>}
            {muscles[0]?.target_low != null && (
              <div aria-hidden="true" className="grid grid-cols-[100px_minmax(0,1fr)_44px] gap-3 text-[11px] font-bold text-muted tabular">
                <span />
                <div className="relative h-4">
                  {[0, muscles[0].target_low, muscles[0].target_max!].map((v) => (
                    <span key={v} className="absolute -translate-x-1/2" style={{ left: `${(v / SCALE_MAX) * 100}%` }}>{v}</span>
                  ))}
                </div>
                <span className="text-right">sets</span>
              </div>
            )}
            <ul aria-label="Hard sets per muscle">
            {muscles.map((m) => {
              const low = m.target_low ?? 0;
              const max = m.target_max ?? 0;
              const tone = m.status === "below" ? "bg-steel-text text-steel-text" : m.status === "above" ? "bg-orange text-orange"
                : m.status === "no_target" ? "bg-[#6f6a62] text-muted" : "bg-chalk text-chalk";
              const [bar, text] = tone.split(" ");
              return (
                <li key={m.muscle} aria-label={`${muscleName(m.muscle)}: ${m.hard_sets} hard sets`}
                  className="group relative grid grid-cols-[100px_minmax(0,1fr)_44px] items-center gap-3 py-2">
                  <span className="text-[13px] font-bold uppercase tracking-[0.06em] transition-colors group-hover:text-orange">{muscleName(m.muscle)}</span>
                  <div className="relative h-3.5 bg-tile-done">
                    {m.target_low != null && (
                      <div aria-hidden="true" className="absolute -inset-y-1 border-x-2 border-muted bg-[#474747]"
                        style={{ left: `${(low / SCALE_MAX) * 100}%`, width: `${((max - low) / SCALE_MAX) * 100}%` }} />
                    )}
                    <div className={`absolute inset-y-[3px] left-0 transition-[width] duration-500 ${bar}`}
                      style={{ width: `${Math.min(100, (m.hard_sets / SCALE_MAX) * 100)}%` }} />
                  </div>
                  <span className={`text-right text-sm font-extrabold tabular ${text}`}>{m.hard_sets}</span>
                  <div className="col-span-3 max-h-0 overflow-hidden text-xs text-muted opacity-0 transition-[max-height,opacity] duration-200 group-hover:max-h-24 group-hover:opacity-100">
                    {m.status === "no_target" ? "Tracked, no target" : m.status === "below" ? "Below target" : m.status === "above" ? "Above max" : m.status === "deload" ? "Deload week" : "In range"}
                    {" · "}
                    {m.contributors.map((c) => `${exerciseName(catalog, c.exercise_id)} ${c.hard_sets}`).join(" · ")}
                  </div>
                </li>
              );
            })}
            </ul>
            {shortfalls.length > 0 && (
              <p className="mt-3 border-l-2 border-steel pl-2 text-xs text-muted">
                Your schedule can&apos;t fit the full starting volume for: {shortfalls.join("; ")}.
              </p>
            )}
          </section>

          {lifts.length > 0 && (
            <section>
              <h3 className="display text-[22px] font-extrabold">Main lifts · estimated max</h3>
              <table className="mt-2.5 w-full text-sm tabular">
                <thead>
                  <tr className="text-[11px] font-bold tracking-[0.1em] text-muted">
                    <th scope="col" className="text-left font-bold">LIFT</th>
                    {tableWeeks.map((w) => <th key={w} scope="col" className="text-right font-bold">W{w}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {lifts.map((l) => {
                    const byWeek = e1rmByWeek(metrics, l.exercise_id);
                    return (
                      <tr key={l.exercise_id}>
                        <th scope="row" className="py-1 text-left font-bold">{exerciseName(catalog, l.exercise_id)}</th>
                        {tableWeeks.map((w, i) => {
                          const v = byWeek.get(w);
                          const prev = i > 0 ? byWeek.get(tableWeeks[i - 1]) : undefined;
                          const up = v !== undefined && prev !== undefined && v > prev;
                          return <td key={w} className={`text-right ${up ? "font-extrabold text-orange" : "text-muted"}`}>{v !== undefined ? Math.round(v) : "—"}</td>;
                        })}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </section>
          )}

        </div>
      </aside>
    </>
  );
}
