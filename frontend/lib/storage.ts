/**
 * Persistence for the lifter's data. The ONLY module that knows where data lives.
 *
 * Today: browser localStorage. Later (Supabase): implement `DataStore` against the
 * database and swap it in where the store is created; nothing else changes.
 */
import type { Profile, Program, SetLog } from "./types";
import { validateSetLog } from "./workout";

export const STORAGE_KEY = "alc:data";
export const BACKUP_PREFIX = "alc:backup:";
export const DATA_VERSION = 1;

export interface AppData {
  version: typeof DATA_VERSION;
  profile: Profile | null;
  program: Program | null;
  logs: SetLog[];
  savedAt: string;
}

export interface DataStore {
  load(): AppData | null;
  /** Returns false if the data could not be saved (storage full or blocked). */
  save(data: AppData): boolean;
  clear(): void;
}

export class DataFormatError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "DataFormatError";
  }
}

export function emptyData(): AppData {
  return { version: DATA_VERSION, profile: null, program: null, logs: [], savedAt: new Date().toISOString() };
}

/**
 * Shape check for anything read from storage or an imported file. Deep validation of
 * programs and logs happens in the backend on the next /metrics call.
 */
export function parseData(raw: unknown): AppData {
  if (typeof raw !== "object" || raw === null) throw new DataFormatError("Not a data file.");
  const d = raw as Record<string, unknown>;
  if (d.version !== DATA_VERSION) {
    throw new DataFormatError(`Unsupported data version ${String(d.version)} (expected ${DATA_VERSION}).`);
  }
  if (!Array.isArray(d.logs)) throw new DataFormatError("Missing training logs.");
  const isObjOrNull = (v: unknown) => v === null || (typeof v === "object" && !Array.isArray(v));
  if (!isObjOrNull(d.profile) || !isObjOrNull(d.program)) throw new DataFormatError("Malformed profile or program.");
  if (d.program && !Array.isArray((d.program as { weeks?: unknown }).weeks)) {
    throw new DataFormatError("Program has no weeks.");
  }
  (d.logs as SetLog[]).forEach((log, i) => {
    const problem = typeof log === "object" && log !== null ? validateSetLog(log) : "not a set";
    if (problem) throw new DataFormatError(`Logged set #${i + 1}: ${problem}.`);
  });
  return {
    version: DATA_VERSION,
    profile: (d.profile as Profile | null) ?? null,
    program: (d.program as Program | null) ?? null,
    logs: d.logs as SetLog[],
    savedAt: typeof d.savedAt === "string" ? d.savedAt : new Date().toISOString(),
  };
}

/** localStorage-backed store. Never throws: storage can be full, blocked or unavailable. */
export function createLocalStore(backend?: Pick<Storage, "getItem" | "setItem" | "removeItem">): DataStore {
  const storage = () => {
    if (backend) return backend;
    try {
      return typeof window === "undefined" ? null : window.localStorage;
    } catch {
      return null; // blocked (e.g. privacy settings)
    }
  };

  return {
    load() {
      let text: string | null | undefined;
      try {
        text = storage()?.getItem(STORAGE_KEY);
      } catch {
        return null; // storage blocked
      }
      if (!text) return null;
      try {
        return parseData(JSON.parse(text));
      } catch {
        // Corrupt or from an unsupported version: keep a copy instead of letting the next save erase it.
        try {
          storage()?.setItem(`${BACKUP_PREFIX}${new Date().toISOString()}`, text);
          storage()?.removeItem(STORAGE_KEY);
        } catch {
          /* ignore */
        }
        return null;
      }
    },
    save(data) {
      const target = storage();
      if (!target) return false;
      try {
        target.setItem(STORAGE_KEY, JSON.stringify({ ...data, savedAt: new Date().toISOString() }));
        return true;
      } catch {
        return false; // quota exceeded or blocked: the app keeps working in memory
      }
    },
    clear() {
      try {
        storage()?.removeItem(STORAGE_KEY);
      } catch {
        /* ignore */
      }
    },
  };
}

/** Export as pretty JSON (download/backup). */
export function exportData(data: AppData): string {
  return JSON.stringify({ ...data, savedAt: new Date().toISOString() }, null, 2);
}

/** Import from exported JSON. Throws DataFormatError with a readable message. */
export function importData(text: string): AppData {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    throw new DataFormatError("That file isn't valid JSON.");
  }
  return parseData(raw);
}
