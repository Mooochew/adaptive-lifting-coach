import path from "node:path";
import { defineConfig, devices } from "@playwright/test";
import { API_URL, FRONTEND_URL } from "./e2e/env";

/**
 * Smoke tests: the real Next.js app against the real FastAPI backend.
 * Both servers start on their own ports (see e2e/env.ts) and the frontend uses its own
 * build folder, so this runs alongside `npm run dev` without clashing.
 */

const backendDir = path.resolve(__dirname, "../backend");
const python = path.join(backendDir, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: FRONTEND_URL,
    actionTimeout: 10_000,   // a missing button fails in 10 s, not the 60 s test timeout
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: `"${python}" -m uvicorn app.main:app --port ${new URL(API_URL).port}`,
      cwd: backendDir,
      url: `${API_URL}/health`,
      // fake = scripted AI coach: e2e runs need no API key and cost nothing
      env: { FRONTEND_ORIGIN: FRONTEND_URL, LLM_PROVIDER: "fake" },
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
    },
    {
      // localhost only: not reachable from other devices on the network
      command: `npx next dev --hostname localhost --port ${new URL(FRONTEND_URL).port}`,
      url: FRONTEND_URL,
      env: { NEXT_PUBLIC_API_URL: API_URL, NEXT_DIST_DIR: ".next-e2e" },
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
});
