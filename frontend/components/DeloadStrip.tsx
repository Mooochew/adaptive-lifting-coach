"use client";

import { useApp } from "@/lib/store";
import { deloadInfo } from "@/lib/view";
import { currentPosition } from "@/lib/workout";

/** Hazard-striped strip under the top bar: scheduled or recommended deloads. */
export default function DeloadStrip() {
  const program = useApp((s) => s.program);
  const logs = useApp((s) => s.logs);
  const metrics = useApp((s) => s.metrics);
  if (!program) return null;

  const info = deloadInfo(program, currentPosition(program, logs).week, metrics);
  if (!info) return null;

  return (
    <div className="flex items-stretch border-b-2 border-line bg-tile-done" role="status">
      <div className="hazard w-7 flex-none" aria-hidden="true" />
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2 px-6 py-3 text-sm">
        <span className={`display text-lg font-extrabold ${info.urgent ? "text-orange" : "text-chalk"}`}>{info.headline}</span>
        <span className="text-muted">{info.detail}</span>
      </div>
    </div>
  );
}
