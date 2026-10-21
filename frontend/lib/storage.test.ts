import { describe, expect, it } from "vitest";
import { BACKUP_PREFIX, createLocalStore, DataFormatError, emptyData, exportData, importData, STORAGE_KEY } from "./storage";
import { benchSets, profile, program } from "./test-fixtures";

function memoryBackend() {
  const map = new Map<string, string>();
  return {
    map,
    getItem: (k: string) => map.get(k) ?? null,
    setItem: (k: string, v: string) => void map.set(k, v),
    removeItem: (k: string) => void map.delete(k),
  };
}

const sample = () => ({ ...emptyData(), profile, program, logs: benchSets(1, [5, 5, 4]) });

describe("local store", () => {
  it("round-trips data", () => {
    const store = createLocalStore(memoryBackend());
    expect(store.save(sample())).toBe(true);
    const loaded = store.load();
    expect(loaded?.program?.weeks).toHaveLength(2);
    expect(loaded?.logs).toHaveLength(3);
  });

  it("still loads data saved before the 2000 weight limit (never moves a user's data to a backup)", () => {
    const store = createLocalStore(memoryBackend());
    const data = sample();
    data.logs[0] = { ...data.logs[0], weight: 2500 };
    store.save(data);
    expect(store.load()?.logs[0].weight).toBe(2500);
  });

  it("returns null when empty", () => {
    expect(createLocalStore(memoryBackend()).load()).toBeNull();
  });

  it("backs up corrupt or unsupported data instead of letting the next save erase it (reviewer m8)", () => {
    const backend = memoryBackend();
    const store = createLocalStore(backend);
    backend.map.set(STORAGE_KEY, JSON.stringify({ version: 99, logs: [] }));
    expect(store.load()).toBeNull();
    const backups = [...backend.map.keys()].filter((k) => k.startsWith(BACKUP_PREFIX));
    expect(backups).toHaveLength(1);
    expect(backend.map.get(backups[0])).toContain('"version":99');
    expect(backend.map.has(STORAGE_KEY)).toBe(false);
  });

  it("reports failed saves instead of hiding them", () => {
    const store = createLocalStore({
      getItem: () => null,
      setItem: () => { throw new Error("QuotaExceededError"); },
      removeItem: () => {},
    });
    expect(store.save(sample())).toBe(false);
  });

  it("clear removes the data", () => {
    const backend = memoryBackend();
    const store = createLocalStore(backend);
    store.save(sample());
    store.clear();
    expect(store.load()).toBeNull();
  });
});

describe("export / import", () => {
  it("round-trips through JSON", () => {
    const back = importData(exportData(sample()));
    expect(back.profile?.goal).toBe("general");
    expect(back.logs).toHaveLength(3);
  });

  it.each([
    ["not json", "That file isn't valid JSON."],
    [JSON.stringify([1, 2]), "Unsupported data version"],
    [JSON.stringify({ version: 1 }), "Missing training logs."],
    [JSON.stringify({ version: 1, logs: [], profile: null, program: { weeks: "x" } }), "Program has no weeks."],
  ])("rejects bad files with a readable message: %s", (text, msg) => {
    expect(() => importData(text)).toThrow(DataFormatError);
    expect(() => importData(text)).toThrow(msg);
  });

  it("rejects a file with an invalid set, naming the set (reviewer m8)", () => {
    const bad = { ...sample(), logs: [benchSets(1, [5])[0], { ...benchSets(1, [5])[0], rpe: 0 }] };
    expect(() => importData(JSON.stringify(bad))).toThrow("Logged set #2: RPE must be 1-10.");
  });
});
