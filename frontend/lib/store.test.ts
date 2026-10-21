import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, type ApiClient } from "./api";
import type { AppData, DataStore } from "./storage";
import { createAppStore } from "./store";
import { benchSets, emptyMetrics, makeProgram, profile, program } from "./test-fixtures";
import type { MetricsRequest } from "./types";

function memoryStorage(failSaves = false): DataStore & { saved: AppData | null } {
  const s = {
    saved: null as AppData | null,
    load: () => s.saved,
    save: (d: AppData) => {
      if (failSaves) return false;
      s.saved = structuredClone(d);
      return true;
    },
    clear: () => { s.saved = null; },
  };
  return s;
}

function fakeApi(overrides: Partial<ApiClient> = {}) {
  const metricsCalls: MetricsRequest[] = [];
  const templateCalls: number[] = [];
  const api: ApiClient = {
    health: async () => ({ ok: true, demo_mode: false }),
    rules: async () => { throw new Error("unused"); },
    exercises: async () => [],
    demo: async () => ({ profile, program, logs: benchSets(1, [5, 5, 4]).map((l) => ({ ...l, id: null })) }),
    template: async (_p, block = 1) => {
      templateCalls.push(block);
      return { program: makeProgram(block), notes: [], volume_shortfalls: block === 1 ? [] : ["biceps: 6 of 10"] };
    },
    advance: async (req) => ({ program: req.program, changes: [], notes: [] }),
    metrics: async (req) => {
      metricsCalls.push(req);
      return { ...emptyMetrics(), sessions_logged: req.logs.length };
    },
    ...overrides,
  };
  return { api, metricsCalls, templateCalls };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("app store", () => {
  it("loads the demo, gives every log an id, saves it, and fetches metrics", async () => {
    const storage = memoryStorage();
    const { api, metricsCalls } = fakeApi();
    const store = createAppStore({ api, storage });

    await store.getState().loadDemo();

    const s = store.getState();
    expect(s.program?.weeks).toHaveLength(2);
    expect(s.logs.every((l) => l.id)).toBe(true);
    expect(s.metricsStatus).toBe("ready");
    expect(metricsCalls).toHaveLength(1);
    expect(metricsCalls[0].goal).toBe("general");
    expect(storage.saved?.logs).toHaveLength(3);
    expect(s.selected).toEqual({ week: 1, day: 1 });       // bench still in progress
  });

  it("applies a week-end advance before computing metrics", async () => {
    const advanced = { ...makeProgram(), adjusted_through: 1 };
    advanced.weeks[1].sessions[0].exercises[0].load = 190;
    const { api, metricsCalls } = fakeApi({
      advance: async () => ({ program: advanced, changes: ["W2 D1 bench: 185 -> 190 lb"], notes: [] }),
    });
    const storage = memoryStorage();
    const store = createAppStore({ api, storage });
    await store.getState().loadDemo();

    expect(store.getState().program?.adjusted_through).toBe(1);
    expect(store.getState().lastChanges).toEqual(["W2 D1 bench: 185 -> 190 lb"]);
    expect(metricsCalls[0].program.adjusted_through).toBe(1);          // metrics used the updated plan
    expect(storage.saved?.program?.adjusted_through).toBe(1);
  });

  it("a new block keeps history and increments the block number (reviewer m4)", async () => {
    const { api, templateCalls } = fakeApi();
    const store = createAppStore({ api, storage: memoryStorage() });
    await store.getState().loadDemo();
    await store.getState().startFromProfile(profile);

    expect(templateCalls).toEqual([2]);
    expect(store.getState().program?.block).toBe(2);
    expect(store.getState().logs).toHaveLength(3);                     // block 1 history kept
    expect(store.getState().volumeShortfalls).toEqual(["biceps: 6 of 10"]);
  });

  it("sends only the current block's logs to the backend (old history can't break refreshes)", async () => {
    const advanceCalls: MetricsRequest[] = [];
    const { api, metricsCalls } = fakeApi({
      advance: async (req) => {
        advanceCalls.push(req);
        return { program: req.program, changes: [], notes: [] };
      },
    });
    const store = createAppStore({ api, storage: memoryStorage() });
    await store.getState().loadDemo();
    await store.getState().startFromProfile(profile);

    expect(store.getState().logs).toHaveLength(3);                      // history still kept locally
    expect(advanceCalls.at(-1)?.logs).toEqual([]);
    expect(metricsCalls.at(-1)?.logs).toEqual([]);
  });

  it("debounces refreshes while logging: three quick edits -> one request", async () => {
    const { api, metricsCalls } = fakeApi();
    const store = createAppStore({ api, storage: memoryStorage(), metricsDebounceMs: 400 });
    await store.getState().loadDemo();
    metricsCalls.length = 0;

    const [a, b, c] = benchSets(2, [5, 5, 5]);
    store.getState().upsertSet(a);
    await vi.advanceTimersByTimeAsync(100);
    store.getState().upsertSet(b);
    await vi.advanceTimersByTimeAsync(100);
    store.getState().upsertSet(c);
    await vi.advanceTimersByTimeAsync(400);

    expect(metricsCalls).toHaveLength(1);
    expect(metricsCalls[0].logs).toHaveLength(6);
  });

  it("refuses to save an invalid set and explains why", async () => {
    const store = createAppStore({ ...fakeApi(), storage: memoryStorage() });
    await store.getState().loadDemo();
    store.getState().upsertSet({ ...benchSets(2, [5])[0], rpe: 0 });
    expect(store.getState().logs).toHaveLength(3);
    expect(store.getState().error).toBe("Can't save that set: RPE must be 1-10.");
  });

  it("a stored set the backend would reject is left out of refreshes, with a banner (app keeps working)", async () => {
    const tooHeavy = { ...benchSets(1, [5])[0], id: "old-typo", weight: 2500 };
    const storage = memoryStorage();
    storage.saved = { version: 1, profile, program, logs: [...benchSets(1, [5, 5]), tooHeavy],
                      savedAt: new Date().toISOString() };
    const { api, metricsCalls } = fakeApi();
    const store = createAppStore({ api, storage });
    store.getState().hydrate();
    await vi.runAllTimersAsync();

    expect(store.getState().logs).toHaveLength(3);                       // nothing deleted
    expect(metricsCalls.at(-1)?.logs.map((l) => l.weight)).not.toContain(2500);
    expect(store.getState().metricsStatus).toBe("ready");
    expect(store.getState().ignoredSets).toBe(
      "1 logged set in this block is ignored for your progress (weight must be 2000 or less).");

    // Fixing the set clears the banner on the next refresh.
    store.getState().upsertSet({ ...tooHeavy, weight: 185 });
    await vi.runAllTimersAsync();
    expect(store.getState().ignoredSets).toBeNull();
  });

  it("a stored set with an exercise the backend no longer knows is left out, in the app's real startup order", async () => {
    const { api, metricsCalls } = fakeApi({
      exercises: async () => [{ id: "bench_press" } as never, { id: "lateral_raise" } as never],
      rules: async () => ({}) as never,
    });
    const storage = memoryStorage();
    storage.saved = { version: 1, profile, program, savedAt: new Date().toISOString(),
                      logs: [...benchSets(1, [5]), { ...benchSets(1, [5])[0], id: "x", exercise_id: "old_lift" }] };
    const store = createAppStore({ api, storage });
    store.getState().hydrate();                       // App.tsx: hydrate first...
    await store.getState().loadCatalog();             // ...then the exercise library arrives
    await vi.runAllTimersAsync();

    expect(metricsCalls.at(-1)?.logs.map((l) => l.exercise_id)).toEqual(["bench_press"]);
    expect(store.getState().ignoredSets).toContain('unknown exercise "old_lift"');
    expect(store.getState().metricsStatus).toBe("ready");
  });

  it("refuses a new set heavier than the backend accepts", async () => {
    const store = createAppStore({ ...fakeApi(), storage: memoryStorage() });
    await store.getState().loadDemo();
    store.getState().upsertSet({ ...benchSets(2, [5])[0], weight: 2005 });
    expect(store.getState().logs).toHaveLength(3);
    expect(store.getState().error).toBe("Can't save that set: weight must be 2000 or less.");
  });

  it("assigns an id to a set saved without one", async () => {
    const store = createAppStore({ ...fakeApi(), storage: memoryStorage() });
    await store.getState().loadDemo();
    store.getState().upsertSet({ ...benchSets(2, [5])[0], id: null });
    expect(store.getState().logs.at(-1)?.id).toBeTruthy();
  });

  it("flags failed saves (reviewer m8)", async () => {
    const store = createAppStore({ ...fakeApi(), storage: memoryStorage(true) });
    await store.getState().loadDemo();
    expect(store.getState().saveFailed).toBe(true);
  });

  it("a slow, outdated refresh never overwrites newer data", async () => {
    // Responses echo how many logs they were computed from; the OLD request is the slow one.
    const slowFor = (req: MetricsRequest) => (req.logs.length === 0 ? 1000 : 10);
    const delayed = <T,>(value: T, ms: number, signal?: AbortSignal) => new Promise<T>((resolve, reject) => {
      signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
      setTimeout(() => resolve(value), ms);
    });
    const { api } = fakeApi({
      advance: (req, signal) => delayed({ program: req.program, changes: [], notes: [] }, slowFor(req), signal),
      metrics: (req, signal) => delayed({ ...emptyMetrics(), sessions_logged: req.logs.length }, slowFor(req), signal),
    });
    const store = createAppStore({ api, storage: memoryStorage() });
    store.setState({ profile, program, logs: [] });

    const first = store.getState().refreshMetrics();          // computed from 0 logs, slow
    store.setState({ logs: benchSets(1, [5, 5, 5]) });
    const second = store.getState().refreshMetrics();         // computed from 3 logs, fast
    await vi.advanceTimersByTimeAsync(2000);
    await Promise.all([first, second]);

    expect(store.getState().metrics?.sessions_logged).toBe(3);
    expect(store.getState().metricsStatus).toBe("ready");
  });

  it("shows a readable error when the backend is down", async () => {
    const { api } = fakeApi({ demo: async () => { throw new ApiError("network", "Can't reach the backend"); } });
    const store = createAppStore({ api, storage: memoryStorage() });
    await store.getState().loadDemo();
    expect(store.getState().error).toBe("Can't reach the backend");
    expect(store.getState().busy).toBeNull();
  });

  it("hydrates saved data on startup", async () => {
    const storage = memoryStorage();
    const first = createAppStore({ ...fakeApi(), storage });
    await first.getState().loadDemo();

    const second = createAppStore({ ...fakeApi(), storage });
    second.getState().hydrate();
    expect(second.getState().hydrated).toBe(true);
    expect(second.getState().logs).toHaveLength(3);
  });

  it("reset clears memory and storage", async () => {
    const storage = memoryStorage();
    const store = createAppStore({ ...fakeApi(), storage });
    await store.getState().loadDemo();
    store.getState().reset();
    expect(store.getState().program).toBeNull();
    expect(storage.saved).toBeNull();
  });

  it("import replaces data; a bad file leaves data untouched and sets an error", async () => {
    const store = createAppStore({ ...fakeApi(), storage: memoryStorage() });
    await store.getState().loadDemo();
    const exported = store.getState().exportJson();

    store.getState().importJson("garbage");
    expect(store.getState().error).toBe("That file isn't valid JSON.");
    expect(store.getState().logs).toHaveLength(3);

    store.getState().reset();
    store.getState().importJson(exported);
    expect(store.getState().logs).toHaveLength(3);
    expect(store.getState().error).toBeNull();
  });

  it("session navigation", () => {
    const store = createAppStore({ ...fakeApi(), storage: memoryStorage() });
    store.getState().openSession({ week: 2, day: 1 });
    expect(store.getState()).toMatchObject({ view: "session", selected: { week: 2, day: 1 } });
    store.getState().closeSession();
    expect(store.getState().view).toBe("board");
  });
});
