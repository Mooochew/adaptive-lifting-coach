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
 *  - the AI coach's notes are asked for once each time a week ends (or on Refresh); a failed
 *    automatic request isn't repeated on every refresh
 */
import { createStore, type StoreApi } from "zustand/vanilla";
import { useStore } from "zustand";
import { api as defaultApi, ApiError, type ApiClient } from "./api";
import { createLocalStore, emptyData, exportData, importData, type AppData, type DataStore } from "./storage";
import type { CoachReview, ExerciseInfo, Metrics, Profile, Program, RulesResponse, SetLog, StoredCoachNotes } from "./types";
import { currentPosition, newId, newSetProblem, removeLog, upsertLog, withIds, type SessionRef } from "./workout";

export type LoadStatus = "idle" | "loading" | "ready" | "error";

export interface AppState {
  hydrated: boolean;
  profile: Profile | null;
  program: Program | null;
  logs: SetLog[];
  /** The AI coach's review of the current program (null for the demo athlete or older data). */
  review: CoachReview | null;
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
  /** The AI coach's note on the latest week (null until a week has ended or Refresh is pressed). */
  coachNotes: StoredCoachNotes | null;
  notesStatus: LoadStatus;
  notesError: string | null;

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
  /** Ask the AI coach for a note on `week` (automatic, when a week ends) or on the latest logged week (Refresh). */
  requestNotes(week?: number): Promise<void>;
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
  let notesTriedFor: string | null = null;   // block:week of the last automatic request
  let dataVersion = 0;                       // bumped whenever all data is replaced: late replies are dropped

  return createStore<AppState>()((set, get) => {
    const snapshot = (): AppData => {
      const { profile, program, logs, review, coachNotes } = get();
      return { ...emptyData(), profile, program, logs, review, coachNotes };
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

    /** This block's logs the backend will accept; the rest are named in `problems`. */
    const usableLogs = (program: Program) => {
      // The backend only uses this block's logs; sending older blocks would let one
      // stale row from long ago fail every refresh. Rows it would reject (saved before a
      // limit existed, or imported) are left out and named in a banner instead.
      const { catalog } = get();
      const catalogLoaded = Object.keys(catalog).length > 0;
      const problems: string[] = [];
      const logs = get().logs.filter((l) => {
        if (l.block !== program.block) return false;
        const problem = newSetProblem(l)
          ?? (catalogLoaded && !catalog[l.exercise_id] ? `unknown exercise "${l.exercise_id}"` : null);
        if (problem) problems.push(problem);
        return !problem;
      });
      return { logs, problems };
    };

    /** A week has ended since the last note: ask once (automatic; Refresh can always ask again). */
    const notesDue = () => {
      const { program, coachNotes } = get();
      if (!program || program.adjusted_through < 1) return false;
      const key = `${program.block}:${program.adjusted_through}`;
      const covered = coachNotes?.block === program.block && coachNotes.adjustedThrough >= program.adjusted_through;
      if (covered || notesTriedFor === key) return false;
      notesTriedFor = key;
      return true;
    };

    /** Replace all lifter data at once (demo, new program, import). */
    const replaceData = (data: Pick<AppData, "profile" | "program" | "logs" | "review" | "coachNotes">) => {
      const logs = withIds(data.logs);
      notesTriedFor = null;            // new data: its finished week may be asked about once
      dataVersion += 1;
      set({
        profile: data.profile,
        program: data.program,
        logs,
        review: data.review ?? null,
        coachNotes: data.coachNotes ?? null,
        notesStatus: "idle",
        notesError: null,
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
      review: null,
      catalog: {},
      rules: null,
      metrics: null,
      metricsStatus: "idle",
      metricsError: null,
      lastChanges: [],
      volumeShortfalls: [],
      coachNotes: null,
      notesStatus: "idle",
      notesError: null,
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
          replaceData({ profile: demo.profile, program: demo.program, logs: demo.logs, review: null });
          set({ volumeShortfalls: [] });
          await get().refreshMetrics();
        } catch (err) {
          set({ error: message(err) });
        } finally {
          set({ busy: null });
        }
      },

      /** New block from a profile, reviewed by the AI coach. Earlier blocks' logs are kept as history. */
      async startFromProfile(profile) {
        set({ busy: "template", error: null });
        try {
          const block = (get().program?.block ?? 0) + 1;
          const res = await api.review(profile, block);
          const { program, volume_shortfalls } = res;
          const review = { source: res.source, summary: res.summary, edits: res.edits, fallbackReason: res.fallback_reason };
          replaceData({ profile, program, logs: get().logs, review });
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
        replaceData({ profile: null, program: null, logs: [], review: null, coachNotes: null });
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
        const { logs, problems } = usableLogs(program);
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
          if (inflight === controller) {
            set({ metrics, metricsStatus: "ready" });
            if (notesDue()) void get().requestNotes(program.adjusted_through);   // the week that just ended
          }
        } catch (err) {
          if (controller.signal.aborted || isAbort(err)) return; // superseded by a newer refresh
          set({ metricsStatus: "error", metricsError: message(err) });
        } finally {
          if (inflight === controller) inflight = null;
        }
      },

      async requestNotes(week) {
        const { profile, program, lastChanges } = get();
        if (!profile || !program || get().notesStatus === "loading") return;
        const asked = dataVersion;
        set({ notesStatus: "loading", notesError: null });
        try {
          const res = await api.coachNotes({ profile, program, logs: usableLogs(program).logs, changes: lastChanges,
                                             ...(week ? { week } : {}) });
          if (dataVersion !== asked) {                            // replaced while the coach was thinking
            set({ notesStatus: "idle" });
            return;
          }
          if (res.source === "none" && res.retryable) {
            // Busy, over the limit, unavailable: not the week's note. Keep whatever is shown.
            set({ notesStatus: "error", notesError: res.reason ?? "The coach couldn't write notes right now." });
            return;
          }
          set({ coachNotes: { ...res, block: program.block, adjustedThrough: program.adjusted_through },
                notesStatus: "ready" });
          persist();
        } catch (err) {
          set(dataVersion === asked ? { notesStatus: "error", notesError: message(err) } : { notesStatus: "idle" });
        }
      },

      exportJson() {
        return exportData(snapshot());
      },

      importJson(text) {
        try {
          // Notes from a file aren't trusted to describe its data; they're written again once a week ends.
          replaceData({ ...importData(text), coachNotes: null });
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
