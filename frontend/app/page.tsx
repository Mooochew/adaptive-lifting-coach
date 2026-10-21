import BackendStatus from "@/components/BackendStatus";

/** Temporary setup check (Phase 2, step 2). Replaced by the Block Board in step 4. */
export default function Home() {
  return (
    <main className="mx-auto w-full max-w-[1440px] px-10 py-16">
      <p className="text-xs font-bold uppercase tracking-[0.16em] text-orange">Phase 2 · setup check</p>
      <h1 className="display mt-2 text-6xl font-black leading-[0.95]">
        Adaptive <span className="text-orange">Lifting</span> Coach
      </h1>
      <p className="mt-4 max-w-xl text-muted">
        Fonts, colors and the API connection. The Block Board replaces this page in step 4.
      </p>
      <div className="mt-10">
        <BackendStatus />
      </div>
    </main>
  );
}
