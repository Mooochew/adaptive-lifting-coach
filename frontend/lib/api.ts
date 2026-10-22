/**
 * Typed client for the FastAPI backend.
 *
 * Every failure becomes an ApiError with a `kind` the UI can act on:
 *   network     backend not running / unreachable
 *   timeout     request took longer than the limit
 *   http        backend answered with an error status (e.g. 422 validation) or an unreadable body
 * A request cancelled by the caller rejects with the browser's AbortError instead.
 */
import { API_URL } from "./config";
import type {
  AdvanceResponse,
  DemoAthlete,
  ExerciseInfo,
  Health,
  Metrics,
  MetricsRequest,
  Profile,
  ReviewResponse,
  RulesResponse,
  TemplateResponse,
} from "./types";

export type ApiErrorKind = "network" | "timeout" | "http";

export class ApiError extends Error {
  constructor(
    public kind: ApiErrorKind,
    message: string,
    public status?: number,
    public detail?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const DEFAULT_TIMEOUT_MS = 15_000;
// A sleeping free-tier server can take ~30 s to wake; /health is how we wake it.
const HEALTH_TIMEOUT_MS = 30_000;
// The AI coach may think and retry (up to 3 model calls) before answering.
const REVIEW_TIMEOUT_MS = 180_000;

type FetchLike = typeof fetch;

export interface ApiClient {
  health(): Promise<Health>;
  rules(): Promise<RulesResponse>;
  exercises(): Promise<ExerciseInfo[]>;
  demo(): Promise<DemoAthlete>;
  template(profile: Profile, block?: number): Promise<TemplateResponse>;
  /** The standard plan reviewed by the AI coach (falls back to the standard plan server-side). */
  review(profile: Profile, block?: number): Promise<ReviewResponse>;
  advance(req: MetricsRequest, signal?: AbortSignal): Promise<AdvanceResponse>;
  metrics(req: MetricsRequest, signal?: AbortSignal): Promise<Metrics>;
}

/** Turn FastAPI's 422 body into one readable line. */
function describeHttpError(status: number, body: unknown): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (Array.isArray(detail) && detail.length) {
    const first = detail[0] as { loc?: unknown[]; msg?: string };
    const where = first.loc?.filter((p) => p !== "body").join(".");
    return `Invalid data${where ? ` at ${where}` : ""}: ${first.msg ?? "validation error"}`;
  }
  if (typeof detail === "string") return detail;
  return `Backend error (${status})`;
}

const abortError = () => new DOMException("The request was cancelled.", "AbortError");

export function createApi(baseUrl: string = API_URL, fetchImpl: FetchLike = fetch): ApiClient {
  async function request<T>(
    path: string,
    init: RequestInit = {},
    { timeoutMs = DEFAULT_TIMEOUT_MS, signal }: { timeoutMs?: number; signal?: AbortSignal } = {},
  ): Promise<T> {
    if (signal?.aborted) throw abortError();

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort("timeout"), timeoutMs);
    const onAbort = () => controller.abort("cancelled");
    signal?.addEventListener("abort", onAbort);

    // Map a failure while fetching OR reading the body to the right error.
    const fail = (): never => {
      if (controller.signal.reason === "timeout") {
        throw new ApiError("timeout", `The backend took longer than ${timeoutMs / 1000}s to respond.`);
      }
      if (controller.signal.reason === "cancelled") throw abortError();
      throw new ApiError("network", `Can't reach the backend at ${baseUrl}. Is it running?`);
    };

    try {
      let res: Response;
      try {
        res = await fetchImpl(`${baseUrl}${path}`, { ...init, signal: controller.signal });
      } catch {
        return fail();
      }

      let text: string;
      try {
        text = await res.text();
      } catch {
        return fail();
      }

      let body: unknown = null;
      try {
        body = text ? JSON.parse(text) : null;
      } catch {
        if (res.ok) throw new ApiError("http", "The backend sent a response that isn't valid JSON.", res.status);
      }
      if (!res.ok) throw new ApiError("http", describeHttpError(res.status, body), res.status, body);
      if (body === null) throw new ApiError("http", "The backend sent an empty response.", res.status);
      return body as T;
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
    }
  }

  // Only requests with a body declare JSON; plain GETs stay "simple" (no CORS preflight).
  const post = <T>(path: string, data: unknown, opts?: { signal?: AbortSignal; timeoutMs?: number }) =>
    request<T>(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    }, opts);

  return {
    health: () => request<Health>("/health", {}, { timeoutMs: HEALTH_TIMEOUT_MS }),
    rules: () => request<RulesResponse>("/rules"),
    exercises: () => request<ExerciseInfo[]>("/exercises"),
    demo: () => request<DemoAthlete>("/demo"),
    template: (profile, block = 1) => post<TemplateResponse>("/program/template", { profile, block }),
    review: (profile, block = 1) =>
      post<ReviewResponse>("/program/review", { profile, block }, { timeoutMs: REVIEW_TIMEOUT_MS }),
    advance: (req, signal) => post<AdvanceResponse>("/program/advance", req, { signal }),
    metrics: (req, signal) => post<Metrics>("/metrics", req, { signal }),
  };
}

export const api = createApi();
