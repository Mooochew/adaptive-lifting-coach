"use client";

import { useApp } from "@/lib/store";
import { btn } from "./ui";

/**
 * The AI coach's note on the latest logged week. The words are the AI's, rendered as plain text
 * and labelled as AI-written; every number in them was checked against the engine's numbers.
 */
export default function CoachNotes() {
  const notes = useApp((s) => s.coachNotes);
  const status = useApp((s) => s.notesStatus);
  const error = useApp((s) => s.notesError);
  const requestNotes = useApp((s) => s.requestNotes);
  const loading = status === "loading";
  const ai = notes?.source === "ai" ? notes : null;

  return (
    <section aria-labelledby="notes-title" aria-busy={loading} className="border-2 border-line bg-tile p-4">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-baseline gap-2">
          <h3 id="notes-title" className="display text-[22px] font-extrabold">Coach notes</h3>
          <span className="border border-steel px-1.5 py-0.5 text-[11px] font-extrabold uppercase tracking-[0.1em] text-muted">
            AI-written
          </span>
        </div>
        <button className={btn()} onClick={() => void requestNotes()} disabled={loading}>
          {loading ? "Thinking…" : notes ? "Refresh" : "Get notes"}
        </button>
      </div>

      {ai ? (
        <div className={`mt-2 transition-opacity duration-200 ${loading ? "opacity-50" : ""}`}>
          <p className="text-[11px] font-bold uppercase tracking-[0.12em] text-muted">
            Week {ai.week} · {ai.in_progress ? "so far" : "finished"}
          </p>
          <p className="mt-1 text-[17px] font-bold leading-snug">{ai.headline}</p>
          <ul className="mt-3 flex flex-col gap-2.5" aria-label="Coach's points">
            {ai.points.map((p, i) => (
              <li key={i} className="border-l-2 border-steel pl-3">
                <span className="text-[11px] font-extrabold uppercase tracking-[0.1em] text-chalk-dim">{p.label}</span>
                <p className="text-sm leading-relaxed text-muted">{p.text}</p>
              </li>
            ))}
          </ul>
          <p className="mt-3 border-l-2 border-orange pl-3 text-sm">
            <span className="text-[11px] font-extrabold uppercase tracking-[0.1em] text-orange">Focus · </span>
            {ai.focus}
          </p>
          <p className="mt-3 text-xs text-faint">Every number here was checked against the engine&apos;s numbers for this week.</p>
        </div>
      ) : (
        <p className="mt-1.5 text-[13px] leading-relaxed text-muted">
          {loading ? "Your coach is reading your week…"
            : status === "error" ? `Couldn't get notes: ${error}`
            : notes?.reason ?? "Notes appear here each time a week ends. You can also ask for a check-in any time."}
        </p>
      )}
      {ai && status === "error" && <p className="mt-2 text-xs text-steel-text">Couldn&apos;t refresh: {error}</p>}
    </section>
  );
}
