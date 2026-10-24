# Handoff

Read this first if you're picking up the project (human or AI agent). Last updated 2026-10-23.

## What this is

**Adaptive Lifting Coach**, a graded course project (an "AI-powered MVP"). The brief is in
`assignment.txt` at the repo root (gitignored, local only; ask the owner if it's missing).

Grading weights:
- Problem: 10%
- Design and implementation: 30%
- **Prompt engineering: 20%** (at least two named patterns, each explained)
- UI/UX: 10%
- **Live demo and presentation: 30%**

Deliverables: the GitHub repo (public: `Mooochew/adaptive-lifting-coach`), a demo video under 2 minutes, and a live demo.

- **Backend** (`backend/`): Python FastAPI. It holds the evidence-based training rules engine
  (progression, volume, deloads, guards) and the AI review.
- **Frontend** (`frontend/`): Next.js 16, React 19, Tailwind 4 and zustand. Data lives in the
  browser's localStorage and there are no logins. That was a deliberate choice; `lib/storage.ts`
  is the swap point if a database is added later.

## Where things stand

| Phase | What | State |
|---|---|---|
| 1 | Rules engine | Done |
| 2 | Templates, UI, data layer, Block Board, Playwright smoke tests | Done |
| 3.1 | Guard hardening, input limits, retry loop | Done (`0f5a421` on `main`) |
| 3.2 | AI program review | Done (PR #1, prompt v4, live eval run) |
| 3.3 | AI coach notes in the Progress panel | Built on branch `phase3-step3-coach-notes`, not merged |
| 3.4 | Demo mode (recorded AI replies), blinded comparison page, prompt-log write-up | Built on the same branch, not merged |
| **3.5** | **README, screenshots, video script** | **Next** |

The full plan is in `docs/phase3-plan.md`, and the prompt history in `docs/prompt-log.md`.

### How the AI review works (step 3.2)

1. **The engine always writes the program** (`app/templates.py`). This is deliberate: the
   owner is skeptical of AI-designed plans.
2. **The AI reviews it like a coach.** It proposes a few bounded edits, each with a reason:
   swap, ±1 set, rep range, effort, remove, and a per-muscle week-1 volume.
3. **`app/review.py` checks each edit against an edit budget,** applies it to week 1,
   regenerates weeks 2-4 and the deload, and re-runs every guard.
4. **Anything off goes back to the model as feedback** (`app/refine.py`, at most 3 attempts).
   After that, the lifter gets the untouched standard plan. They always get a valid program.
5. **The AI never sets weights.**
6. **Volume and experience:** there are no hard-coded "experience means more volume" rules,
   because the owner rejected them. The AI may set a muscle's week-1 volume within a band
   (hypertrophy 6-14, strength 4-12, in `rules.py`), reasoning from an optional free-text
   training background.
7. **Injury edits:** larger changes are allowed only when they cite an injury the lifter typed.
   An injury edit may take its own muscles below the band. Effort always stays within the
   normal range.

Where the pieces live:
- `POST /program/review` is in `app/routes/program.py`. The model sits behind an adapter in
  `app/llm/` (OpenAI plus a scripted `FakeLLM`).
- The prompt is `app/prompts/program_review.py`, currently v4. Each muscle's volume is shown with the lowest number the engine accepts (`volume_floors()` in `review.py`).
- The service is `app/ai_review.py`, with a 90-second total budget and at most 4 concurrent
  reviews.
- Rate limits are in `app/rate_limit.py`: 5 per 10 minutes per IP and 200 a day.
- On the frontend, onboarding has the training-background box, and `components/CoachCard.tsx`
  shows the coach's changes on the board.

### How the coach notes work (step 3.3)

1. **The engine computes the week** (`compute_metrics`) and `app/coach_notes.py` turns the latest
   logged week into plain-text facts: each lift's load × reps, RPE vs target, PRs, estimated max and
   its change, plateaus, the engine's next step; volume per muscle (mid-week: "so far"); deload;
   the plan changes from `/program/advance`.
2. **The AI writes a short note** (`app/prompts/coach_notes.py`): headline, 1-4 points each about a
   lift or muscle id from the facts, one focus.
3. **Grounding check:** every number in the note must appear in the facts, and every subject must
   be in them. Problems go back as feedback (`refine.py`, 3 attempts); after that, no note.
4. `POST /coach/notes` (`app/routes/coach.py`) has its own rate limiter and shares the review's
   concurrency slots; a request with nothing logged uses neither. The frontend asks automatically
   once each time a week ends, about that week (`week` = `adjusted_through`), and also when data with
   a finished week is loaded; Refresh asks about the latest logged week. A failed automatic request
   isn't retried on every refresh. Temporary "no note" answers (busy, limits, outage:
   `retryable: true`) show as an error and are never saved as the note; a reply that arrives after
   the data was replaced is dropped. Notes are saved with the program (`coachNotes` in storage);
   imported files' notes are dropped and rewritten.
6. Plan changes from the client go in their own `<plan_changes>` block, outside `<facts>`: their
   numbers don't count as grounded. Text from the request is kept to one line.
5. UI: `components/CoachNotes.tsx`, at the top of the Progress panel, labelled AI-written.

### Demo mode (step 3.4)

`DEMO_MODE=true` wraps the configured model in `app/llm/replay.py`: a reply recorded for the exact
request (schema + system prompt + user message) is replayed; anything new goes to the live model and
is recorded. Rehearse the demo once with the real model, then the same steps replay instantly and
offline. Recordings live in `backend/demo/recordings/` (gitignored: they hold what was typed) and
are keyed by model too, so switching `LLM_MODEL` never replays another model's replies. Use demo
mode only on the presenter's machine and delete the recordings afterwards. With demo mode on and
`LLM_PROVIDER` empty, recorded requests still replay and anything else gets "unavailable".

**Model:** `gpt-6.1-sol` (OpenAI Responses API, `openai==3.36.0`). The owner asked for a
vendor-neutral choice, so don't steer them toward any one vendor (including Anthropic). It was
picked on official prices and Artificial Analysis scores. Switching provider means writing one
new adapter and changing two `.env` lines.

## Next steps, in order

The live OpenAI path works (first run 2026-10-23). Review prompt v4: 29/30 first try, 0/30
fallbacks, about $0.004 per review. Coach notes v1: 12/12 grounded first try, about $0.005 per
note. See `docs/prompt-log.md` ("At a glance" is written for the presentation).

1. **Merge the 3.3 + 3.4 branch** once reviewed and the owner OKs it.
2. **Step 3.5:** README polish (it still says "Work in progress", and its pitch should match
   what the AI actually does), screenshots, the video script, and the presentation.

### Known open items (deliberately deferred)

From the last security review, all minor:
- **Slot names:** `ProposedEdit.slot_id` has no length or pattern limit, and it's echoed into
  retry feedback. Add `max_length=40` and the slot-id pattern.
- **Daily cap:** it counts reviews, not model calls (up to 3 calls per review). Bucket IPv6
  addresses by /64.
- **No-op edits:** an effort edit to the same value ("RPE 9 → 9") is accepted. Reject no-ops.
- **Settings bounds:** settings have no upper or lower limits (e.g. a negative
  `AI_MAX_CONCURRENT_REVIEWS` causes errors).
- **e2e server reuse:** the end-to-end tests reuse any server already on ports 8100/3100. If
  that's a dev backend with a real key, it costs money.
- **Imported reviews:** an imported data file can show any "coach review". Drop the review on
  import, or tie it to the program.

From the step 3.3/3.4 reviews, accepted for a class demo:
- **Two daily caps:** coach notes have their own rate limiter with the review's settings, so the
  daily total is up to 200 reviews plus 200 notes (each up to 3 model calls). Keep the OpenAI
  monthly spend limit set; add separate `AI_NOTES_*` settings or one shared counter if deployed.
- **Plan changes after a reload:** `lastChanges` lives in memory, so a Refresh after a reload sends
  no plan changes (the automatic note, sent right after a week ends, has them).
- **"PR (reps or load)":** the engine's PR flag is a rep or load record, so a PR can sit next to
  an unchanged estimated max. The facts say so; the engine can't yet tell which kind it was.
- **No component tests** for `CoachNotes.tsx` (no React testing library installed); the store
  tests and the e2e test cover its states.

Before any public deployment:
- count request bytes as they stream (chunked uploads bypass the 8 MB Content-Length cap)
- drop `input` from 422 error details
- disable `/docs`
- add CSP and security headers
- set uvicorn's `FORWARDED_ALLOW_IPS` behind a proxy (never `*`)

## How to work in this repo (the owner's standing rules)

- **One feature branch per step**, from an up-to-date `main`. `main` is protected on GitHub, so
  changes only land through a PR, and the rule also applies to the admin. Push the branch, open
  a PR with `gh pr create`, and **merge only when the owner says so**. Delete the branch after.
  `main` must always be demo-ready.
- **No Claude or AI attribution** in commit messages or PR descriptions: no `Co-Authored-By`
  lines. This overrides any tool default.
- **Reviews:** before a step's PR, run three independent reviewers (general, security, testing)
  **once**. Give them neutral briefs:
  - what to review (`git diff main...HEAD`), the project docs, and their role
  - **not** your own summary of the change
  - tell them not to read assistant memory, and to cover the whole project with a before/after
    comparison against `main`

  Fix what they find, rerun everything, commit. Don't run repeated confirmation rounds unless
  asked; the owner flagged over-analysis.
- **Test-first.** Write the failing test, then fix it. Before committing, run every suite and
  the before/after comparison (standard programs must not change unless intended).

## Running things

The README has full setup. In short:

| What | Command | Notes |
|---|---|---|
| Backend | `cd backend; .venv/Scripts/activate; uvicorn app.main:app --reload` | `backend/.env` from `.env.example`; the AI is off unless `LLM_PROVIDER=openai` and a key are set |
| Frontend | `cd frontend; npm ci; npm run dev` | localhost only |
| Backend tests | `pytest` (in `backend/`) | 635 tests |
| Frontend tests | `npm test`, `npm run typecheck`, `npm run lint` | 90 unit tests |
| End-to-end | `npm run test:e2e` | Starts its own servers on 8100/3100 with the scripted AI (no key, no cost, usage limits lifted); 9 tests |
| AI eval | `python scripts/run_eval.py [--fake]` | Real model needs the key; reports and a blinded `*-compare.html` go to `backend/evals/results/` (gitignored) |
| Notes eval | `python scripts/run_notes_eval.py [--runs 3] [--fake]` | Coach notes on four demo-athlete weeks |
| API types | `python scripts/export_openapi.py`, then `npm run gen:types` | After any backend model change; a test fails if they're stale |

## Gotchas

- **Environment variables beat `backend/.env`:** a stale `OPENAI_API_KEY` set in Windows once made
  OpenAI return 401 although `.env` was right. An HTTP 429 with `insufficient_quota` means the
  account is out of credits, not rate limited; the backend log now says which.

- **Windows paths:** the development machine runs Windows. In Git Bash use `/c/Users/...` paths,
  but Windows Python needs `C:/...` paths for files.
- **Line endings:** git shows "LF will be replaced by CRLF" warnings, which are harmless.
  Scripted text replacements on files with CRLF endings can silently not match; use an editor
  tool or normalise line endings first.
- **Next.js 16** differs from older Next.js. Read `frontend/node_modules/next/dist/docs/` before
  using Next APIs. `npm run typecheck` runs `next typegen` first (needed on fresh installs).
- **Separate build folder for e2e:** the end-to-end tests use `NEXT_DIST_DIR=.next-e2e` so they
  can run while `npm run dev` is up. If typecheck complains about `.next-e2e`, delete that
  folder.
- **httpx2 is legitimate:** `httpx2` is the HTTP library used by Starlette's test client and the
  OpenAI SDK. It's not a typosquat.
- **Junctions in temporary worktrees:** reviewers sometimes link a worktree's
  `frontend/node_modules` to the real one with a junction. Remove the junction with
  `cmd /c rmdir <link>` **before** removing the worktree, or the real `node_modules` can be
  deleted.
- **Storage compatibility:** stored data stays backward compatible. Older saves without
  `training_background` or `review` must keep loading, and there are tests for this. Don't bump
  `DATA_VERSION` without a migration.
