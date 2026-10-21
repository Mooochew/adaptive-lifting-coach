"use client";

import { useEffect, useState } from "react";
import { API_URL } from "@/lib/config";

type Status = { state: "checking" } | { state: "ok"; demoMode: boolean } | { state: "down" };

/** Pings /health so it's obvious when the Python backend isn't running. */
export default function BackendStatus() {
  const [status, setStatus] = useState<Status>({ state: "checking" });

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${API_URL}/health`, { signal: controller.signal })
      .then((res) => (res.ok ? res.json() : Promise.reject(res.status)))
      .then((body: { demo_mode: boolean }) => setStatus({ state: "ok", demoMode: body.demo_mode }))
      .catch(() => {
        if (!controller.signal.aborted) setStatus({ state: "down" });
      });
    return () => controller.abort();
  }, []);

  const label =
    status.state === "checking" ? "Checking backend…" : status.state === "ok" ? "Backend connected" : "Backend not reachable";

  return (
    <div className="inline-flex items-center gap-4 border-2 border-line bg-tile px-5 py-4">
      <span
        aria-hidden="true"
        className={`size-3 ${status.state === "ok" ? "bg-orange" : status.state === "down" ? "bg-steel" : "bg-faint"}`}
      />
      <div>
        <p className="display text-xl font-extrabold">{label}</p>
        <p className="text-sm text-muted">
          {status.state === "down"
            ? `Start it from backend/: uvicorn app.main:app --reload (expected at ${API_URL})`
            : API_URL}
        </p>
      </div>
    </div>
  );
}
