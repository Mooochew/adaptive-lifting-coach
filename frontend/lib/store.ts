/**
 * App state (zustand). Holds the lifter's profile, program and logs, plus the
 * metrics the backend computed from them.
 *
 * Rules:
 *  - every data change is saved through the DataStore (storage.ts)
 *  - nothing training-related is computed here. After edits (debounced so typing
 *    reps doesn't fire a request per keystroke) the store asks the backend to
 *    (1) advance the program if a week has ended, then (2) compute metrics.
 *    A newer refresh cancels an older one, so a slow response can't overwrite fresh data.
 *  - only finished sets are saved: a saved `completed: false` set means a FAILED set
 */
import { createStore, type StoreApi } from "zustand/vanilla";
import { useStore } from "zustand";
import { api as defaultApi, ApiError, type ApiClient } from "./api";
import { createLocalStore, emptyData, exportData, importData, type AppData, type DataStore } from "./storage";
import type { ExerciseInfo, Metrics, Profile, Program, RulesResponse, SetLog } from "./types";
import { currentPosition, newId, newSetProblem, removeLog, upsertLog, withIds, type SessionRef } from "./workout";

export type LoadStatus = "idle" | "loading" | "ready" | "error";

export interface AppState {
  hydrated: boolean;
  profile: Profile | null;
  program: Program | null;
  logs: SetLog[];
  /** Exercise library by id (names, kind, muscles), loaded once from the backend. */
  catalog: Record<string, ExerciseInfo>;
  /** Training rules (warm-up scheme, load steps, ...), loaded once from the backend. */
  rules: RulesResponse | null;

  metrics: Metrics | null;
  metricsStatus: LoadStatus;
  metricsError: string | null;
  /** Prescription changes from the last time a week ended, e.g. "W3 D1 bench: 185 -> 190 lb". */
  lastChanges: string[];
  /** Muscles the schedule can't bring to the starting volume (from the template). */
  volumeShortfalls: string[];

  busy: "demo" | "template" | null;
  error: string | null;
  /** True when the last save to storage failed (storage full or blocked). */
  saveFailed: boolean;
  /** Logged sets in this block the backend would reject (e.g. saved before a limit existed), left out of refreshes. */
  ignoredSets: string | null;

  view: "board" | "session";
  selected: SessionRef | null;

  hydrate(): void;
  /** Load the exercise library and training rules (once). */
  loadCatalog(): Promise<void>;
  loadDemo(): Promise<void>;
  startFromProfile(profile: Profile): Promise<void>;
  reset(): void;
  openSession(ref: SessionRef): void;
  closeSession(): void;
  upsertSet(log: SetLog): void;
  removeSet(log: SetLog): void;
  refreshMetrics(): Promise<void>;
  exportJson(): string;
  importJson(text: string): void;
  clearError(): void;
}

export interface StoreDeps {
  api?: ApiClient;
  storage?: DataStore;
  metricsDebounceMs?: number;
}

const message = (err: unknown) =>
  err instanceof ApiError || err instanceof Error ? err.message : "Something went wrong.";

const isAbort = (err: unknown) => err instanceof DOMException && err.name === "AbortError";

export function createAppStore({
  api = defaultApi,
  storage = createLocalStore(),
  metricsDebounceMs = 400,
}: StoreDeps = {}): StoreApi<AppState> {
  let debounceTimer: ReturnType<typeof setTimeout> | null = null;
  let inflight: AbortController | null = null;

  return createStore<AppState>()((set, get) => {
    const snapshot = (): AppData => {
      const { profile, program, logs } = get();
      return { ...emptyData(), profile, program, logs };
    };
    const persist = () => {
      const ok = storage.save(snapshot());
      if (ok === get().saveFailed) set({ saveFailed: !ok });
    };

    const scheduleRefresh = () => {
      if (debounceTimer) clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        debounceTimer = null;
        void get().refreshMetrics();
      }, metricsDebounceMs);
    };

    /** Replace all lifter data at once (demo, new program, import). */
    const replaceData = (data: Pick<AppData, "profile" | "program" | "logs">) => {
      const logs = withIds(data.logs);
      set({
        profile: data.profile,
        program: data.program,
        logs,
        metrics: null,
        metricsStatus: "idle",
        metricsError: null,
        lastChanges: [],
        view: "board",
        selected: data.program ? currentPosition(data.program, logs) : null,
      });
      persist();
    };

    return {
      hydrated: false,
      profile: null,
      program: null,
      logs: [],
      catalog: {},
      rules: null,
      metrics: null,
      metricsStatus: "idle",
      metricsError: null,
      lastChanges: [],
      volumeShortfalls: [],
      busy: null,
      error: null,
      saveFailed: false,
      ignoredSets: null,
      view: "board",
      selected: null,

      hydrate() {
        if (get().hydrated) return;
        const data = storage.load();
        if (data) replaceData(data);
        set({ hydrated: true });
        if (data?.program) void get().refreshMetrics();
      },

      async loadCatalog() {
        if (Object.keys(get().catalog).length && get().rules) return;
        try {
          const [list, rules] = await Promise.all([api.exercises(), api.rules()]);
          set({ catalog: Object.fromEntries(list.map((e) => [e.id, e])), rules });
          // Startup refreshes before the library arrives can't spot retired exercise ids; redo it now.
          if (get().program) void get().refreshMetrics();
        } catch {
          // Names fall back to ids and warm-up suggestions are skipped; the backend status explains the outage.
        }
      },

      async loadDemo() {
        set({ busy: "demo", error: null });
        try {
          const demo = await api.demo();
          replaceData({ profile: demo.profile, program: demo.program, logs: demo.logs });
          set({ volumeShortfalls: [] });
          await get().refreshMetrics();
        } catch (err) {
          set({ error: message(err) });
        } finally {
          set({ busy: null });
        }
      },

      /** New block from a profile. Earlier blocks' logs are kept as history. */
      async startFromProfile(profile) {
        set({ busy: "template", error: null });
        try {
          const block = (get().program?.block ?? 0) + 1;
          const { program, volume_shortfalls } = await api.template(profile, block);
          replaceData({ profile, program, logs: get().logs });
          set({ volumeShortfalls: volume_shortfalls });
          await get().refreshMetrics();
        } catch (err) {
          set({ error: message(err) });
        } finally {
          set({ busy: null });
        }
      },

      reset() {
        if (debounceTimer) clearTimeout(debounceTimer);
        inflight?.abort();
        replaceData({ profile: null, program: null, logs: [] });
        storage.clear(); // replaceData persisted an empty snapshot; leave storage truly empty
        set({ volumeShortfalls: [], saveFailed: false, error: null, ignoredSets: null });
      },

      openSession(ref) {
        set({ view: "session", selected: ref });
      },

      closeSession() {
        set({ view: "board" });
      },

      upsertSet(log) {
        const row = log.id ? log : { ...log, id: newId() };
        const problem = newSetProblem(row);
        if (problem) {
          set({ error: `Can't save that set: ${problem}.` });
          return;
        }
        set({ logs: upsertLog(get().logs, row) });
        persist();
        scheduleRefresh();
      },

      removeSet(log) {
        set({ logs: removeLog(get().logs, log) });
        persist();
        scheduleRefresh();
      },

      async refreshMetrics() {
        const { profile } = get();
        let { program } = get();
        if (!profile || !program) return;
        // The backend only uses this block's logs; sending older blocks would let one
        // stale row from long ago fail every refresh. Rows it would reject (saved before a
        // limit existed, or imported) are left out and named in a banner instead.
        const block = program.block;
        const { catalog } = get();
        const catalogLoaded = Object.keys(catalog).length > 0;
        const problems: string[] = [];
        const logs = get().logs.filter((l) => {
          if (l.block !== block) return false;
          const problem = newSetProblem(l)
            ?? (catalogLoaded && !catalog[l.exercise_id] ? `unknown exercise "${l.exercise_id}"` : null);
          if (problem) problems.push(problem);
          return !problem;
        });
        const n = problems.length;
        set({ ignoredSets: n ? `${n} logged set${n === 1 ? " in this block is" : "s in this block are"} ` +
                                `ignored for your progress (${problems[0]}).` : null });

        inflight?.abort();
        const controller = new AbortController();
        inflight = controller;
        set({ metricsStatus: "loading", metricsError: null });

        try {
          // 1. If a week has ended, carry the engine's suggestions into the following weeks.
          const advanced = await api.advance({ goal: profile.goal, program, logs }, controller.signal);
          if (inflight !== controller) return;
          if (advanced.program.adjusted_through !== program.adjusted_through) {
            program = advanced.program;
            set({ program, lastChanges: advanced.changes });
            persist();
          }
          // 2. Metrics for the (possibly updated) program.
          const metrics = await api.metrics({ goal: profile.goal, program, logs }, controller.signal);
          if (inflight === controller) set({ metrics, metricsStatus: "ready" });
        } catch (err) {
          if (controller.signal.aborted || isAbort(err)) return; // superseded by a newer refresh
          set({ metricsStatus: "error", metricsError: message(err) });
        } finally {
          if (inflight === controller) inflight = null;
        }
      },

      exportJson() {
        return exportData(snapshot());
      },

      importJson(text) {
        try {
          replaceData(importData(text));
          set({ error: null, volumeShortfalls: [] });
          void get().refreshMetrics();
        } catch (err) {
          set({ error: message(err) });
        }
      },

      clearError() {
        set({ error: null });
      },
    };
  });
}

/** The app's single store (client-side). Call `hydrate()` once on mount. */
export const appStore = createAppStore();

export function useApp<T>(selector: (state: AppState) => T): T {
  return useStore(appStore, selector);
}
