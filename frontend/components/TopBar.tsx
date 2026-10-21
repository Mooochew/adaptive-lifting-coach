"use client";

import { useRef } from "react";
import { useApp } from "@/lib/store";
import { btn } from "./ui";

interface Props {
  progressOpen: boolean;
  onToggleProgress(): void;
  onNewProgram(): void;
}

const GOAL_LABEL = { strength: "Strength", hypertrophy: "Hypertrophy", general: "General" } as const;

export default function TopBar({ progressOpen, onToggleProgress, onNewProgram }: Props) {
  const profile = useApp((s) => s.profile);
  const program = useApp((s) => s.program);
  const view = useApp((s) => s.view);
  const busy = useApp((s) => s.busy);
  const closeSession = useApp((s) => s.closeSession);
  const loadDemo = useApp((s) => s.loadDemo);
  const exportJson = useApp((s) => s.exportJson);
  const importJson = useApp((s) => s.importJson);
  const reset = useApp((s) => s.reset);
  const fileInput = useRef<HTMLInputElement>(null);
  const menu = useRef<HTMLDetailsElement>(null);

  const closeMenu = () => menu.current?.removeAttribute("open");

  const download = () => {
    const blob = new Blob([exportJson()], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = Object.assign(document.createElement("a"), {
      href: url,
      download: `lifting-coach-${new Date().toISOString().slice(0, 10)}.json`,
    });
    a.click();
    URL.revokeObjectURL(url);
    closeMenu();
  };

  const upload = async (file: File | undefined) => {
    if (file) importJson(await file.text());
    if (fileInput.current) fileInput.current.value = "";
    closeMenu();
  };

  const confirmReset = () => {
    closeMenu();
    if (window.confirm("Delete your program and every logged set from this browser? This can't be undone.")) reset();
  };

  return (
    <header className="flex flex-wrap items-center justify-between gap-4 border-b-2 border-line bg-rubber-inset px-10 py-[18px]">
      <div className="flex flex-wrap items-baseline gap-x-[18px] gap-y-1">
        <span className="display text-[30px] font-black leading-none">
          Adaptive <span className="text-orange">Lifting</span> Coach
        </span>
        {profile && program && (
          <span className="text-xs font-bold uppercase tracking-[0.14em] text-muted">
            Block {program.block} · {GOAL_LABEL[profile.goal]} · {profile.days_per_week} days · {program.units}
          </span>
        )}
      </div>

      <nav className="flex flex-wrap items-center gap-2.5" aria-label="App">
        {program && view === "session" && (
          <button className={btn()} onClick={closeSession}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="square" aria-hidden="true"><path d="M15 6l-6 6 6 6" /></svg>
            Board
          </button>
        )}
        {program && (
          <button className={btn(progressOpen)} onClick={onToggleProgress} aria-expanded={progressOpen}>
            Progress
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="square" aria-hidden="true"><path d="M4 18l5-6 4 3 7-9" /></svg>
          </button>
        )}

        <details ref={menu} className="relative">
          <summary className={`${btn()} list-none [&::-webkit-details-marker]:hidden`} aria-label="Data menu">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="square" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16" /></svg>
          </summary>
          <div className="absolute right-0 top-[calc(100%+6px)] z-40 flex w-60 flex-col border-2 border-line bg-rubber-deep py-1 shadow-[0_6px_0_var(--color-shadow)]">
            <MenuItem onClick={() => { closeMenu(); void loadDemo(); }} disabled={busy !== null}>Load demo athlete</MenuItem>
            <MenuItem onClick={() => { closeMenu(); onNewProgram(); }}>{program ? "Start a new block" : "Build my program"}</MenuItem>
            <MenuItem onClick={download} disabled={!program}>Export data (.json)</MenuItem>
            <MenuItem onClick={() => fileInput.current?.click()}>Import data…</MenuItem>
            <MenuItem onClick={confirmReset} disabled={!program} danger>Reset everything</MenuItem>
          </div>
        </details>
        <input ref={fileInput} type="file" accept="application/json,.json" className="hidden"
          onChange={(e) => void upload(e.target.files?.[0])} aria-label="Import data file" />
      </nav>
    </header>
  );
}

function MenuItem({ children, onClick, disabled, danger }: {
  children: React.ReactNode; onClick(): void; disabled?: boolean; danger?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`min-h-11 px-4 text-left text-sm font-semibold transition-colors hover:bg-tile disabled:cursor-not-allowed disabled:text-faint ${danger ? "text-orange" : "text-chalk"}`}
    >
      {children}
    </button>
  );
}
