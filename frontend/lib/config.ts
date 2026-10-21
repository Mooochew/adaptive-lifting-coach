/** Backend base URL. Set NEXT_PUBLIC_API_URL in frontend/.env.local; inlined at build time. */
export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
