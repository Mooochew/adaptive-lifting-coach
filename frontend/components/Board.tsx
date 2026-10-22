"use client";

import { useApp } from "@/lib/store";
import type { Program, Week } from "@/lib/types";
import { exerciseName, isTimed, loadLabel, prSessions, repRange } from "@/lib/view";
import { currentPosition, sessionProgress, sessionStatus } from "@/lib/workout";
import CoachCard from "./CoachCard";
import E1rmStrip from "./E1rmStrip";

/** The block as a whiteboard: weeks are columns, sessions are tiles. */
export default function Board() {
  const program = useApp((s) => s.program)!;
  const logs = useApp((s) => s.logs);
  const metrics = useApp((s) => s.metrics);
  const catalog = useApp((s) => s.catalog);
  const openSession = useApp((s) => s.openSession);

  const now = currentPosition(program, logs);
  const prs = prSessions(metrics);
  const weeks = program.weeks.slice().sort((a, b) => a.week - b.week);

  return (
    <section className="animate-board-in" aria-labelledby="board-title">
      <div className="mb-[18px] flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 id="board-title" className="display text-[44px] font-black leading-[0.95]">The Board</h1>
          <p className="mt-1.5 text-muted">Hover a session to peek at it. Click to zoom in and log.</p>
        </div>
        <div className="flex gap-[18px] text-xs font-bold uppercase tracking-[0.08em] text-muted">
          <span className="inline-flex items-center gap-1.5"><PrBadge />New rep or load PR</span>
          <span className="inline-flex items-center gap-1.5"><span className="inline-block size-3.5 border-2 border-orange" />This week</span>
        </div>
      </div>

      <CoachCard />

      <div className="overflow-x-auto pb-2">
        <div className="grid min-w-[900px] gap-3" style={{ gridTemplateColumns: `repeat(${weeks.length}, minmax(0, 1fr))` }}>
          {weeks.map((week) => (
            <WeekColumn key={week.week} week={week} program={program} isNow={week.week === now.week}>
              {week.sessions.slice().sort((a, b) => a.day - b.day).map((session) => {
                const ref = { week: week.week, day: session.day };
                const status = sessionStatus(program, logs, ref);
                const progress = sessionProgress(program, logs, ref);
                const statusText =
                  status === "done" ? "Done"
                    : status === "in_progress" ? `In progress · ${progress.exercisesDone}/${progress.exercises}`
                      : week.is_deload ? "Light week" : "Planned";
                return (
                  <button
                    key={session.day}
                    onClick={() => openSession(ref)}
                    aria-label={`Week ${week.week} ${session.focus}, ${statusText}. Open session.`}
                    className={`group relative flex min-h-[108px] flex-col gap-1.5 border-2 p-3.5 pb-3 text-left transition-[transform,box-shadow,border-color,background-color] duration-200 ease-[var(--ease-snap)] hover:z-10 hover:-translate-y-[5px] hover:border-orange hover:bg-tile-hover hover:shadow-[0_7px_0_var(--color-shadow)] focus-visible:z-10 focus-visible:-translate-y-[5px] focus-visible:border-orange focus-visible:outline-none ${
                      status === "in_progress" ? "border-orange bg-tile" : status === "done" ? "border-line bg-tile-done" : "border-line bg-tile"}`}
                  >
                    <span className="flex items-center justify-between gap-2">
                      <span className="text-[11px] font-extrabold tracking-[0.14em] text-muted">DAY {session.day}</span>
                      {prs.has(`${week.week}-${session.day}`) && <PrBadge />}
                    </span>
                    <span className={`display text-[22px] font-extrabold leading-none ${status === "done" ? "text-chalk-dim" : ""}`}>
                      {session.focus}
                    </span>
                    <span className={`text-xs font-bold uppercase tracking-[0.08em] ${status === "in_progress" ? "text-orange" : "text-muted"}`}>
                      {statusText}
                    </span>
                    <span className="flex max-h-0 flex-col gap-[3px] overflow-hidden opacity-0 transition-[max-height,opacity,margin] duration-200 group-hover:mt-1.5 group-hover:max-h-60 group-hover:opacity-100 group-focus-visible:mt-1.5 group-focus-visible:max-h-60 group-focus-visible:opacity-100">
                      {session.exercises.map((ex) => (
                        <span key={ex.slot_id} className="flex justify-between gap-2 border-t border-line pt-1 text-xs text-muted">
                          <span className="truncate">{exerciseName(catalog, ex.exercise_id)}</span>
                          <span className="whitespace-nowrap text-chalk tabular">
                            {ex.sets}×{repRange(ex, isTimed(catalog, ex.exercise_id))}
                            {" · "}{loadLabel(ex.load, program.units, catalog, ex.exercise_id)?.replace(` ${program.units}`, "") ?? "—"}
                          </span>
                        </span>
                      ))}
                    </span>
                  </button>
                );
              })}
            </WeekColumn>
          ))}
        </div>
      </div>

      <E1rmStrip weeks={weeks} currentWeek={now.week} />
    </section>
  );
}

function WeekColumn({ week, program, isNow, children }: {
  week: Week; program: Program; isNow: boolean; children: React.ReactNode;
}) {
  const sub = week.is_deload ? "Recover" : week.week <= program.adjusted_through ? "Complete" : isNow ? "This week" : "Next";
  return (
    <div className={`flex min-w-0 flex-col gap-3 p-3 ${week.is_deload ? "hazard-soft outline-2 -outline-offset-2 outline-dashed outline-line" : ""} ${isNow ? "outline-2 -outline-offset-2 outline-orange" : ""}`}>
      <div className="flex items-baseline justify-between px-0.5 pb-1">
        <span className="display text-[28px] font-black">{week.is_deload ? "Deload" : `W${week.week}`}</span>
        <span className="text-[11px] font-bold uppercase tracking-[0.12em] text-muted">{sub}</span>
      </div>
      {children}
    </div>
  );
}

function PrBadge() {
  return <span className="bg-orange px-1.5 py-0.5 text-[11px] font-extrabold tracking-[0.1em] text-ink">PR</span>;
}
