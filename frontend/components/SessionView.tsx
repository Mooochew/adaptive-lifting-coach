"use client";

import { useState } from "react";
import { useApp } from "@/lib/store";
import { getSession, orderedSessions, sessionProgress } from "@/lib/workout";
import ExerciseRow from "./ExerciseRow";
import { btn } from "./ui";

/** One session, zoomed in from the board. */
export default function SessionView() {
  const program = useApp((s) => s.program)!;
  const logs = useApp((s) => s.logs);
  const ref = useApp((s) => s.selected)!;
  const openSession = useApp((s) => s.openSession);
  const session = getSession(program, ref);
  const [open, setOpen] = useState<string | null>(session?.exercises[0]?.slot_id ?? null);

  if (!session) return <p className="text-muted">That session isn&apos;t in this program.</p>;

  const week = program.weeks.find((w) => w.week === ref.week)!;
  const progress = sessionProgress(program, logs, ref);
  const order = orderedSessions(program);
  const i = order.findIndex((r) => r.week === ref.week && r.day === ref.day);
  const prev = order[i - 1];
  const next = order[i + 1];
  const go = (r: typeof ref) => {
    openSession(r);
    setOpen(getSession(program, r)?.exercises[0]?.slot_id ?? null);
  };

  return (
    <section key={`${ref.week}-${ref.day}`} className="animate-zoom-in" aria-labelledby="session-title">
      <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs font-extrabold uppercase tracking-[0.16em] text-orange">
            Week {ref.week} · Day {ref.day}{week.is_deload ? " · Deload" : ""}
          </p>
          <h1 id="session-title" className="display mt-1 text-[56px] font-black leading-[0.95]">{session.focus}</h1>
        </div>
        <div className="flex flex-wrap items-end gap-7">
          <Stat label="Sets done" value={`${progress.done} / ${progress.total}`} />
          <Stat label="Exercises done" value={`${progress.exercisesDone} / ${progress.exercises}`} />
          <div className="flex gap-2">
            <button className={btn()} onClick={() => prev && go(prev)} disabled={!prev} aria-label="Previous session">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="square" aria-hidden="true"><path d="M15 6l-6 6 6 6" /></svg>
            </button>
            <button className={btn()} onClick={() => next && go(next)} disabled={!next} aria-label="Next session">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="square" aria-hidden="true"><path d="M9 6l6 6-6 6" /></svg>
            </button>
          </div>
        </div>
      </div>

      <div className="flex flex-col gap-2.5">
        {session.exercises.map((ex) => (
          <ExerciseRow key={ex.slot_id} ex={ex} sessionRef={ref} isOpen={open === ex.slot_id}
            onToggle={() => setOpen((cur) => (cur === ex.slot_id ? null : ex.slot_id))} />
        ))}
      </div>
    </section>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[11px] font-bold uppercase tracking-[0.12em] text-muted">{label}</div>
      <div className="display text-[34px] font-black tabular">{value}</div>
    </div>
  );
}
