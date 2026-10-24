import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, type ApiClient } from "./api";
import type { AppData, DataStore } from "./storage";
import { createAppStore } from "./store";
import { benchSets, emptyMetrics, makeProgram, profile, program } from "./test-fixtures";
import type { CoachNotesRequest, MetricsRequest, Profile } from "./types";

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
  const reviewCalls: { profile: Profile; block: number }[] = [];
  const notesCalls: CoachNotesRequest[] = [];
  const api: ApiClient = {
    health: async () => ({ ok: true, demo_mode: false }),
    rules: async () => { throw new Error("unused"); },
    exercises: async () => [],
    demo: async () => ({ profile, program, logs: benchSets(1, [5, 5, 4]).map((l) => ({ ...l, id: null })) }),
    template: async (_p, block = 1) => {
      templateCalls.push(block);
      return { program: makeProgram(block), notes: [], volume_shortfalls: block === 1 ? [] : ["biceps: 6 of 10"] };
    },
    review: async (p, block = 1) => {
      reviewCalls.push({ profile: p, block });
      return { program: makeProgram(block), notes: [], volume_shortfalls: block === 1 ? [] : ["biceps: 6 of 10"],
               source: "ai", summary: "One shoulder-friendly swap.", fallback_reason: null,
               edits: [{ kind: "swap", day: 1, slot_id: "bench", muscle: null, for_injury: true,
                         change: "Barbell Bench Press → Dumbbell Bench Press", reason: "Easier on your shoulder." }] };
    },
    advance: async (req) => ({ program: req.program, changes: [], notes: [] }),
    metrics: async (req) => {
      metricsCalls.push(req);
      return { ...emptyMetrics(), sessions_logged: req.logs.length };
    },
    coachNotes: async (req) => {
      notesCalls.push(req);
      return { source: "ai", week: 1, in_progress: false, headline: "Bench is moving.", focus: "Hit 5 on every set.",
               points: [{ subject: "bench_press", label: "Barbell Bench Press", text: "You held 185 lb." }],
               reason: null, retryable: false, attempts: 1 };
    },
    ...overrides,
  };
  return { api, metricsCalls, templateCalls, reviewCalls, notesCalls };
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
    const { api, reviewCalls } = fakeApi();
    const store = createAppStore({ api, storage: memoryStorage() });
    await store.getState().loadDemo();
    await store.getState().startFromProfile(profile);

    expect(reviewCalls.map((c) => c.block)).toEqual([2]);
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

  it("a new program is reviewed by the AI coach; the review is kept and saved", async () => {
    const storage = memoryStorage();
    const { api, reviewCalls } = fakeApi();
    const store = createAppStore({ api, storage });
    await store.getState().startFromProfile({ ...profile, training_background: "6 years lifting" });

    expect(reviewCalls[0].profile.training_background).toBe("6 years lifting");
    const review = store.getState().review;
    expect(review?.source).toBe("ai");
    expect(review?.summary).toBe("One shoulder-friendly swap.");
    expect(review?.edits[0].reason).toBe("Easier on your shoulder.");
    expect(storage.saved?.review?.summary).toBe("One shoulder-friendly swap.");
  });

  it("the standard plan's note is kept when the AI couldn't help", async () => {
    const { api } = fakeApi({
      review: async () => ({ program: makeProgram(1), notes: [], volume_shortfalls: [], source: "baseline",
                             summary: "", edits: [], fallback_reason: "The AI coach is unavailable right now." }),
    });
    const store = createAppStore({ api, storage: memoryStorage() });
    await store.getState().startFromProfile(profile);
    expect(store.getState().review).toMatchObject({ source: "baseline", fallbackReason: "The AI coach is unavailable right now." });
  });

  it("the demo athlete and a reset have no coach review", async () => {
    const store = createAppStore({ ...fakeApi(), storage: memoryStorage() });
    await store.getState().startFromProfile(profile);
    await store.getState().loadDemo();
    expect(store.getState().review).toBeNull();
    await store.getState().startFromProfile(profile);
    store.getState().reset();
    expect(store.getState().review).toBeNull();
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

  describe("coach notes", () => {
    const weekEnded = () => {
      const advanced = { ...makeProgram(), adjusted_through: 1 };
      return { advance: async () => ({ program: advanced, changes: ["W2 D1 bench: 185 -> 190 lb"], notes: [] }) };
    };

    it("asks for notes once a week ends, with the plan changes, and saves them", async () => {
      const storage = memoryStorage();
      const { api, notesCalls } = fakeApi(weekEnded());
      const store = createAppStore({ api, storage });
      await store.getState().loadDemo();
      await vi.runAllTimersAsync();
      expect(notesCalls).toHaveLength(1);
      expect(notesCalls[0].changes).toEqual(["W2 D1 bench: 185 -> 190 lb"]);
      expect(notesCalls[0].week).toBe(1);                       // the week that just ended
      expect(notesCalls[0].logs.every((l) => l.block === 1)).toBe(true);
      expect(store.getState().coachNotes).toMatchObject({ headline: "Bench is moving.", block: 1, adjustedThrough: 1 });
      expect(storage.saved?.coachNotes?.headline).toBe("Bench is moving.");

      await store.getState().refreshMetrics();                  // same week: no new call
      await vi.runAllTimersAsync();
      expect(notesCalls).toHaveLength(1);
    });

    it("no notes before any week has ended, until asked", async () => {
      const { api, notesCalls } = fakeApi();
      const store = createAppStore({ api, storage: memoryStorage() });
      await store.getState().loadDemo();
      await vi.runAllTimersAsync();
      expect(notesCalls).toHaveLength(0);
      await store.getState().requestNotes();                    // the Refresh button
      expect(notesCalls).toHaveLength(1);
      expect(notesCalls[0].week).toBeUndefined();               // Refresh: the latest logged week
      expect(store.getState().notesStatus).toBe("ready");
    });

    it("a failed request doesn't retry on every refresh, but Refresh retries", async () => {
      let fail = true;
      const { api, notesCalls } = fakeApi({
        ...weekEnded(),
        coachNotes: async (req) => {
          notesCalls.push(req);
          if (fail) throw new ApiError("network", "Can't reach the backend.");
          return { source: "ai", week: 1, in_progress: false, headline: "Back on track.", focus: "Keep going.",
                   points: [], reason: null, retryable: false, attempts: 1 };
        },
      });
      const store = createAppStore({ api, storage: memoryStorage() });
      await store.getState().loadDemo();
      await vi.runAllTimersAsync();
      expect(store.getState().notesStatus).toBe("error");
      expect(store.getState().notesError).toBe("Can't reach the backend.");
      await store.getState().refreshMetrics();
      await vi.runAllTimersAsync();
      expect(notesCalls).toHaveLength(1);
      fail = false;
      await store.getState().requestNotes();
      expect(store.getState().coachNotes?.headline).toBe("Back on track.");
    });

    it("a refresh that gets no note (e.g. over the limit) keeps the note already there", async () => {
      let limited = false;
      const { api } = fakeApi({
        ...weekEnded(),
        coachNotes: async () => limited
          ? { source: "none", week: null, in_progress: false, headline: "", points: [], focus: "",
              reason: "You've reached the limit of coach notes for now.", retryable: true, attempts: 0 }
          : { source: "ai", week: 1, in_progress: false, headline: "Bench is moving.", focus: "x", points: [],
              reason: null, retryable: false, attempts: 1 },
      });
      const store = createAppStore({ api, storage: memoryStorage() });
      await store.getState().loadDemo();
      await vi.runAllTimersAsync();
      limited = true;
      await store.getState().requestNotes();
      expect(store.getState().coachNotes?.headline).toBe("Bench is moving.");
      expect(store.getState().notesStatus).toBe("error");
      expect(store.getState().notesError).toContain("limit");
    });

    it("a temporary 'no note' (busy, limit, outage) is an error, never saved as the week's note", async () => {
      let busy = true;
      const { api, notesCalls } = fakeApi({
        ...weekEnded(),
        coachNotes: async (req) => {
          notesCalls.push(req);
          return busy
            ? { source: "none", week: null, in_progress: false, headline: "", points: [], focus: "",
                reason: "The AI coach is busy right now.", retryable: true, attempts: 0 }
            : { source: "ai", week: 1, in_progress: false, headline: "Got it.", focus: "x", points: [],
                reason: null, retryable: false, attempts: 1 };
        },
      });
      const storage = memoryStorage();
      const store = createAppStore({ api, storage });
      await store.getState().loadDemo();
      await vi.runAllTimersAsync();
      expect(store.getState().coachNotes).toBeNull();
      expect(store.getState()).toMatchObject({ notesStatus: "error", notesError: "The AI coach is busy right now." });
      expect(storage.saved?.coachNotes ?? null).toBeNull();
      busy = false;
      await store.getState().requestNotes();
      expect(store.getState().coachNotes?.headline).toBe("Got it.");
    });

    it("a permanent 'no note' (nothing logged yet) is kept, so it isn't asked again", async () => {
      const { api, notesCalls } = fakeApi({
        ...weekEnded(),
        coachNotes: async (req) => {
          notesCalls.push(req);
          return { source: "none", week: null, in_progress: false, headline: "", points: [], focus: "",
                   reason: "Log a week of training first.", retryable: false, attempts: 0 };
        },
      });
      const store = createAppStore({ api, storage: memoryStorage() });
      await store.getState().loadDemo();
      await vi.runAllTimersAsync();
      expect(store.getState().coachNotes?.reason).toBe("Log a week of training first.");
      await store.getState().refreshMetrics();
      await vi.runAllTimersAsync();
      expect(notesCalls).toHaveLength(1);
    });

    it("asking twice while the coach is thinking sends one request", async () => {
      const { api, notesCalls } = fakeApi();
      const store = createAppStore({ api, storage: memoryStorage() });
      await store.getState().loadDemo();
      await Promise.all([store.getState().requestNotes(), store.getState().requestNotes()]);
      expect(notesCalls).toHaveLength(1);
    });

    it("a late note is dropped when the data was replaced, even with the same block number", async () => {
      let answer: (v: Awaited<ReturnType<ApiClient["coachNotes"]>>) => void = () => {};
      const { api } = fakeApi({ coachNotes: () => new Promise((resolve) => { answer = resolve; }) });
      const store = createAppStore({ api, storage: memoryStorage() });
      await store.getState().loadDemo();
      const exported = store.getState().exportJson();
      const pending = store.getState().requestNotes();
      store.getState().importJson(exported);                     // block 1 again
      answer({ source: "ai", week: 1, in_progress: false, headline: "For the old data.", focus: "x", points: [],
               reason: null, retryable: false, attempts: 1 });
      await pending;
      expect(store.getState().coachNotes).toBeNull();
      expect(store.getState().notesStatus).toBe("idle");
    });

    it("a note that arrives after a new block started is dropped, not stuck loading", async () => {
      let answer: (v: Awaited<ReturnType<ApiClient["coachNotes"]>>) => void = () => {};
      const { api } = fakeApi({ coachNotes: () => new Promise((resolve) => { answer = resolve; }) });
      const store = createAppStore({ api, storage: memoryStorage() });
      await store.getState().loadDemo();
      const pending = store.getState().requestNotes();
      await store.getState().startFromProfile(profile);            // block 2
      answer({ source: "ai", week: 1, in_progress: false, headline: "Old block.", focus: "x", points: [],
               reason: null, retryable: false, attempts: 1 });
      await pending;
      expect(store.getState().coachNotes).toBeNull();
      expect(store.getState().notesStatus).toBe("idle");
    });

    it("notes survive a reload, and an import or a new block drops them", async () => {
      const storage = memoryStorage();
      const { api } = fakeApi(weekEnded());
      const store = createAppStore({ api, storage });
      await store.getState().loadDemo();
      await vi.runAllTimersAsync();
      const exported = store.getState().exportJson();

      const reloaded = createAppStore({ api: fakeApi().api, storage });
      reloaded.getState().hydrate();
      expect(reloaded.getState().coachNotes?.headline).toBe("Bench is moving.");

      const { api: quiet } = fakeApi();
      const imported = createAppStore({ api: quiet, storage: memoryStorage() });
      imported.getState().importJson(exported);
      expect(imported.getState().coachNotes).toBeNull();          // a file's notes aren't trusted...
      const { api: again, notesCalls: askedAgain } = fakeApi(weekEnded());
      const fresh = createAppStore({ api: again, storage: memoryStorage() });
      fresh.getState().importJson(exported);
      await vi.runAllTimersAsync();
      expect(askedAgain).toHaveLength(1);                           // ...so they're written again

      await reloaded.getState().startFromProfile(profile);
      expect(reloaded.getState().coachNotes).toBeNull();
    });
  });

  it("session navigation", () => {
    const store = createAppStore({ ...fakeApi(), storage: memoryStorage() });
    store.getState().openSession({ week: 2, day: 1 });
    expect(store.getState()).toMatchObject({ view: "session", selected: { week: 2, day: 1 } });
    store.getState().closeSession();
    expect(store.getState().view).toBe("board");
  });
});
