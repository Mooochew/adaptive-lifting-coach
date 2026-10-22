"use client";

import { useApp } from "@/lib/store";

/**
 * The AI coach's review of the current program: a summary and each change with its reason.
 * "change" is written by the engine from the program; the summary and reasons are the AI's own
 * words, rendered as plain text and labelled as AI-written.
 */
export default function CoachCard() {
  const review = useApp((s) => s.review);
  if (!review) return null;

  if (review.source === "baseline") {
    return (
      <section aria-label="Coach adjustments" className="mb-5 border-2 border-line bg-tile-done px-5 py-3.5">
        <p className="text-sm text-muted">
          <span className="font-bold uppercase tracking-[0.08em] text-chalk-dim">Standard plan · </span>
          {review.fallbackReason ?? "This is the standard evidence-based plan."}
        </p>
      </section>
    );
  }

  return (
    <section aria-labelledby="coach-title" className="mb-5 border-2 border-line bg-tile px-5 py-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="coach-title" className="display text-[22px] font-extrabold">Your coach&apos;s adjustments</h2>
        <span className="border border-steel px-1.5 py-0.5 text-[11px] font-extrabold uppercase tracking-[0.1em] text-muted">
          AI-written
        </span>
      </div>
      <p className="mt-1.5 max-w-3xl text-[15px] leading-relaxed">{review.summary}</p>
      {review.edits.length > 0 ? (
        <ul className="mt-3 flex flex-col gap-2" aria-label="Changes to the standard plan">
          {review.edits.map((edit, i) => (
            <li key={i} className="border-l-2 border-orange pl-3">
              <span className="font-bold">{edit.change}</span>
              {edit.for_injury && (
                <span className="ml-2 bg-rubber-inset px-1.5 py-0.5 text-[11px] font-extrabold uppercase tracking-[0.08em] text-orange">
                  Injury
                </span>
              )}
              <p className="text-sm text-muted">{edit.reason}</p>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-2 text-sm text-muted">No changes: the standard plan already fits you.</p>
      )}
    </section>
  );
}
