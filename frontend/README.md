# Frontend

Next.js (App Router, TypeScript, Tailwind v4). All training math comes from the Python backend.

```powershell
npm ci                           # exact versions from package-lock.json
copy .env.example .env.local     # points at the backend (default http://localhost:8000)
npm run dev                      # http://localhost:3000
```

Any npm 10+ works for `npm ci`. Adding or upgrading packages with npm 11.5.x can crash
(`Cannot read properties of null (reading 'edgesOut')`, an npm bug); use a newer npm for that,
e.g. `npx npm@latest install <pkg>`.

| Script | What it does |
|---|---|
| `npm run dev` | Dev server |
| `npm run build` | Production build |
| `npm run lint` / `npm run typecheck` | ESLint / TypeScript checks |
| `npm test` | Unit tests (Vitest) for the api client, storage, store and workout helpers |
| `npm run gen:types` | Regenerate `lib/api-types.ts` from `backend/openapi.json` |
| `npm run audit:prod` | Vulnerability check of what ships to users (dev tools excluded) |

**Known dev-only advisory:** `npm audit` reports GHSA-vfj7-8cjw-p6xm in `braces`, pulled in by
`eslint-config-next`. No patched release exists yet, it only runs inside ESLint on our own
patterns, and `npm audit fix --force` would downgrade the lint config to Next 14. Production
dependencies are clean (`npm run audit:prod`). Re-check with `npm update` when `braces` is patched.

When a backend model changes: `python scripts/export_openapi.py` (in `backend/`, venv active), then `npm run gen:types`.

Design tokens (Rubber & Steel) live in `app/globals.css`.
