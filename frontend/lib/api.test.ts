import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, createApi } from "./api";
import { profile } from "./test-fixtures";

const json = (status: number, body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

const asFetch = (fn: unknown) => fn as typeof fetch;

afterEach(() => vi.useRealTimers());

describe("api client", () => {
  it("posts JSON with the block number and returns the parsed body", async () => {
    const fetchMock = vi.fn(() => json(200, { program: { weeks: [] }, notes: [], volume_shortfalls: [] }));
    const api = createApi("http://api.test", asFetch(fetchMock));
    const res = await api.template(profile, 2);
    expect(res.notes).toEqual([]);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://api.test/program/template");
    expect(init.method).toBe("POST");
    const body = JSON.parse(init.body as string);
    expect(body.block).toBe(2);
    expect(body.profile.goal).toBe("general");
  });

  it("asks the AI coach for a reviewed program, allowing it time to think", async () => {
    vi.useFakeTimers();
    const body = { program: { weeks: [] }, notes: [], volume_shortfalls: [], source: "ai", summary: "Hi",
                   edits: [], fallback_reason: null };
    // Answers after `ms`, unless the request is aborted first (like a real fetch).
    const slowFetch = (ms: number) => vi.fn((_url: string, init: RequestInit) => new Promise<Response>((resolve, reject) => {
      const timer = setTimeout(() => resolve(new Response(JSON.stringify(body), { status: 200 })), ms);
      init.signal?.addEventListener("abort", () => { clearTimeout(timer); reject(new DOMException("aborted", "AbortError")); });
    }));

    const fetchMock = slowFetch(40_000);
    const pending = createApi("http://api.test", asFetch(fetchMock)).review(profile, 3);
    await vi.advanceTimersByTimeAsync(40_000);
    const res = await pending;
    expect(res.summary).toBe("Hi");

    const template = createApi("http://api.test", asFetch(slowFetch(40_000))).template(profile).catch((e) => e);
    await vi.advanceTimersByTimeAsync(40_000);
    expect((await template).kind).toBe("timeout");                 // ordinary calls still give up at 15 s

    const stuck = createApi("http://api.test", asFetch(slowFetch(200_000))).review(profile).catch((e) => e);
    await vi.advanceTimersByTimeAsync(121_000);
    expect((await stuck).kind).toBe("timeout");                    // and a stuck review at 120 s
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://api.test/program/review");
    expect(JSON.parse(init.body as string)).toMatchObject({ block: 3, profile: { goal: "general" } });
  });

  it("asks for coach notes with the profile, program, logs and plan changes", async () => {
    const fetchMock = vi.fn(() => json(200, { source: "none", week: null, in_progress: false, headline: "",
                                               points: [], focus: "", reason: "Log a week", retryable: false, attempts: 0 }));
    const res = await createApi("http://api.test", asFetch(fetchMock))
      .coachNotes({ profile, program: { weeks: [] } as never, logs: [], changes: ["x"] });
    expect(res.reason).toBe("Log a week");
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://api.test/coach/notes");
    expect(JSON.parse(init.body as string)).toMatchObject({ changes: ["x"], profile: { goal: "general" } });
  });

  it("GET requests don't send a Content-Type (no CORS preflight)", async () => {
    const fetchMock = vi.fn(() => json(200, { ok: true, demo_mode: false }));
    await createApi("http://api.test", asFetch(fetchMock)).health();
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.headers).toBeUndefined();
  });

  it("turns a 422 into a readable validation message", async () => {
    const api = createApi("http://api.test", asFetch(() =>
      json(422, { detail: [{ loc: ["body", "logs", 0, "rpe"], msg: "Input should be less than or equal to 10" }] })));
    const err = await api.health().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.kind).toBe("http");
    expect(err.status).toBe(422);
    expect(err.message).toBe("Invalid data at logs.0.rpe: Input should be less than or equal to 10");
  });

  it("treats a 200 with an unreadable body as an error, not success (reviewer m3)", async () => {
    const api = createApi("http://api.test", asFetch(() =>
      Promise.resolve(new Response("<html>proxy error</html>", { status: 200 }))));
    const err = await api.demo().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.message).toContain("isn't valid JSON");
  });

  it("reports a network error when the backend is down", async () => {
    const api = createApi("http://api.test", asFetch(() => Promise.reject(new TypeError("fetch failed"))));
    const err = await api.demo().catch((e) => e);
    expect(err.kind).toBe("network");
    expect(err.message).toContain("Is it running?");
  });

  it("does not send a request whose signal is already aborted", async () => {
    const fetchMock = vi.fn(() => json(200, {}));
    const controller = new AbortController();
    controller.abort();
    const err = await createApi("http://api.test", asFetch(fetchMock))
      .metrics({ goal: "general", program: { block: 1, units: "lb", adjusted_through: 0, weeks: [] }, logs: [] },
        controller.signal)
      .catch((e) => e);
    expect(err.name).toBe("AbortError");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("times out slow requests", async () => {
    vi.useFakeTimers();
    const hang = (_url: string, init: RequestInit) =>
      new Promise<Response>((_, reject) => init.signal?.addEventListener("abort", () => reject(new Error("aborted"))));
    const api = createApi("http://api.test", asFetch(hang));
    const pending = api.demo().catch((e) => e);
    await vi.advanceTimersByTimeAsync(15_000);
    const err = await pending;
    expect(err.kind).toBe("timeout");
  });
});
