"use client";

import { useEffect, useState } from "react";
import { useApp } from "@/lib/store";
import Banners from "./Banners";
import Board from "./Board";
import DeloadStrip from "./DeloadStrip";
import Onboarding from "./Onboarding";
import ProgressPanel from "./ProgressPanel";
import SessionView from "./SessionView";
import TopBar from "./TopBar";
import Welcome from "./Welcome";

/** The whole app: one surface that switches between board, session and onboarding in place. */
export default function App() {
  const hydrated = useApp((s) => s.hydrated);
  const program = useApp((s) => s.program);
  const view = useApp((s) => s.view);
  const hydrate = useApp((s) => s.hydrate);
  const loadCatalog = useApp((s) => s.loadCatalog);
  const [progressOpen, setProgressOpen] = useState(false);
  const [onboarding, setOnboarding] = useState(false);

  useEffect(() => {
    hydrate();
    void loadCatalog();
  }, [hydrate, loadCatalog]);

  let main: React.ReactNode;
  if (!hydrated) {
    main = <p className="py-24 text-center text-muted">Loading…</p>;
  } else if (onboarding) {
    main = <Onboarding onDone={() => setOnboarding(false)} onCancel={program ? () => setOnboarding(false) : undefined} />;
  } else if (!program) {
    main = <Welcome onStart={() => setOnboarding(true)} />;
  } else if (view === "session") {
    main = <SessionView />;
  } else {
    main = <Board />;
  }

  return (
    <div className="relative flex min-h-screen flex-col overflow-x-hidden">
      <TopBar
        progressOpen={progressOpen}
        onToggleProgress={() => setProgressOpen((o) => !o)}
        onNewProgram={() => setOnboarding(true)}
      />
      {program && !onboarding && <DeloadStrip />}
      <Banners />
      <main className="mx-auto w-full max-w-[1440px] flex-1 px-10 pb-12 pt-7">{main}</main>
      {program && <ProgressPanel open={progressOpen} onClose={() => setProgressOpen(false)} />}
    </div>
  );
}
