"use client";

import { useApp } from "@/lib/store";
import BackendStatus from "./BackendStatus";
import { btn, btnPrimary } from "./ui";

/** First screen when there's no program yet. */
export default function Welcome({ onStart }: { onStart(): void }) {
  const busy = useApp((s) => s.busy);
  const loadDemo = useApp((s) => s.loadDemo);

  return (
    <section className="animate-board-in py-10">
      <p className="text-xs font-bold uppercase tracking-[0.16em] text-orange">Science-based training that adapts</p>
      <h1 className="display mt-2 max-w-3xl text-6xl font-black leading-[0.95]">
        A training block that adapts to what you actually lift.
      </h1>
      <p className="mt-5 max-w-2xl text-lg text-muted">
        Log your sets and effort. Every session, the coach decides whether to add reps, add weight,
        hold, or back off, and tells you why. Weight only goes up once you&apos;ve earned it.
      </p>
      <div className="mt-9 flex flex-wrap gap-3">
        <button className={btnPrimary} onClick={onStart}>Build my program</button>
        <button className={btn()} onClick={() => void loadDemo()} disabled={busy !== null}>
          {busy === "demo" ? "Loading demo…" : "Load demo athlete"}
        </button>
      </div>
      <div className="mt-12">
        <BackendStatus />
      </div>
    </section>
  );
}
