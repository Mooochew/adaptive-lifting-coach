"use client";

import { useApp } from "@/lib/store";

/** Errors and warnings the lifter should see: action failures, metrics failures, failed saves. */
export default function Banners() {
  const error = useApp((s) => s.error);
  const metricsError = useApp((s) => s.metricsError);
  const saveFailed = useApp((s) => s.saveFailed);
  const clearError = useApp((s) => s.clearError);
  const refreshMetrics = useApp((s) => s.refreshMetrics);

  if (!error && !metricsError && !saveFailed) return null;

  return (
    <div className="mx-auto mt-4 flex w-full max-w-[1440px] flex-col gap-2 px-10" role="alert">
      {error && (
        <Banner text={error} action={{ label: "Dismiss", onClick: clearError }} />
      )}
      {metricsError && (
        <Banner text={`Couldn't update your progress: ${metricsError}`} action={{ label: "Retry", onClick: () => void refreshMetrics() }} />
      )}
      {saveFailed && (
        <Banner text="Your browser blocked saving. Changes are kept until you close this tab; use Export data to keep a copy." />
      )}
    </div>
  );
}

function Banner({ text, action }: { text: string; action?: { label: string; onClick(): void } }) {
  return (
    <div className="flex items-center justify-between gap-4 border-2 border-orange bg-rubber-deep px-4 py-3 text-sm">
      <span>{text}</span>
      {action && (
        <button onClick={action.onClick} className="min-h-9 shrink-0 px-3 text-xs font-bold uppercase tracking-[0.08em] text-orange hover:text-orange-hover">
          {action.label}
        </button>
      )}
    </div>
  );
}
