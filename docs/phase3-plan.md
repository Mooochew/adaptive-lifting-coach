# Phase 3 plan: the AI coach

Status: **planning** (2026-10-21). Branch for step 2: `phase3-step2-ai-program-review`.

## Where things stand

| Phase | What | State |
|---|---|---|
| 1 | Rules engine (FastAPI): progression, guards, volume, metrics | Done |
| 2 | Templates, Next.js UI, data layer, Block Board, Playwright smoke tests | Done |
| 3.1 | Guard hardening, input limits, retry loop (`backend/app/refine.py`) | Done (`0f5a421`) |
| **3.2** | **AI program review** (this plan) | Planned |
| 3.3 | AI coach notes in the Progress panel | Later |
| 3.4 | Demo mode (recorded AI responses) + prompt-log write-up | Later |
| 3.5 | README, screenshots, handoff, video script | Later |

The app has no LLM calls yet. The course brief (`assignment.txt`, local only) grades prompt
engineering (20%) and the live demo (30%), so steps 3.2–3.4 matter most.

## Step 3.2 — design: the engine writes the program, the AI reviews it like a coach

AI-generated workout plans tend to be generic, too high in volume, and poorly grounded in
research. On the other hand, good programming is nuanced: experience doesn't simply mean more
sets, and some experienced lifters grow better on less. Hard-coding rules like "advanced = more
volume" would bake in the same oversimplification. So:

1. The **rules engine** builds the baseline program: evidence-based, deterministic, the same
   starting volume for everyone (today's behaviour), and it already passes every guard.
2. The **AI reviews** the baseline together with the lifter's profile (goal, experience,
   equipment, time, recent sets, injury notes, training background) and returns a short list of
   **proposed edits**, each with a reason, plus a short coaching summary. This is where the
   coach-like judgment lives, e.g. lowering volume for an experienced lifter who recovers
   better on less.
3. The engine checks every edit against an **edit budget**, applies the allowed ones across the
   whole block, then runs the full guards again.
4. Edits that break a rule are sent back with the reason (Iterative Refinement, max 3 attempts).
   If the AI still can't produce valid edits, or is unavailable, the lifter gets the **baseline**.
   The lifter always gets a valid program.

The AI never sets weights. Starting loads come from the lifter's recent sets via the existing
formula, and the server sets units, block number and progression state.

### Edit budget

| Edit | Normal | Only when it cites an injury the lifter entered |
|---|---|---|
| Swap exercise | Only to an alternative the template already lists for that slot, with available equipment | Any library exercise with available equipment that shares a primary muscle |
| Sets on one exercise | ±1, within volume limits | May reduce further or remove the exercise |
| Rep range | Within the rules' range for that lift | Same |
| Target effort (RPE) | Within the allowed range | May go lower (easier) |
| **Muscle starting volume** | Set a muscle's week-1 weekly sets within the band below (≤ 3 muscles) | Same band |
| Count | ≤ 4 exercise edits; ≤ 1 edit per slot; ≤ 3 volume edits | Not counted toward the limits |

**Volume band for week 1 (weekly hard sets per muscle):**

| Goal | Baseline today | AI may choose |
|---|---|---|
| Hypertrophy | 10–12 | 6–14 |
| Strength | 6–10 | 4–12 |

The AI decides *how much*; the engine decides *where* (the same distribution code that builds the
baseline adds or removes sets across sessions). Weeks 2–4 then ramp by the normal rules and the
deload cuts volume as usual. Below about 4–6 weekly hard sets, growth reliably drops off; the top
of the band keeps week 1 well under the weekly maximum so there's room to ramp.

"Injury" edits are rejected when the profile has no injury text. Each edit needs a reason
(≤ 200 characters). The summary is ≤ 400 characters. Both are shown in the UI labelled as
AI-written.

### Extra checks on the edited program

Every exercise uses available equipment; days per week unchanged; each session fits the time
budget; week-1 volume per muscle stays within the band; then the existing guards (session cap,
weekly max, ramp, deload rules, loads).

### New onboarding input: training background (optional)

A free-text box, max 500 characters, e.g. "6 years lifting; high volume burns me out; I respond
better to fewer, harder sets." The AI reads it alongside the experience level. Like the injury
notes, it's untrusted input: it can inform edits but can never relax a guard. Stored in the
profile (`training_background`), sent only to our backend.

## Model: GPT-6.1 Sol, behind a provider adapter

Chosen 2026-10-21 after a vendor-neutral comparison (official pricing pages + Artificial
Analysis). GPT-6.1 Sol (`gpt-6.1-sol`, $2 / $10 per 1M tokens) scored 48 on the AA
Intelligence Index at medium effort, against Claude Opus 5.5 at 51 ($4 / $20) and Claude
Sonnet 5.5 at 41 ($2 / $10). It also had the lowest measured cost to run the index ($361).
Expected cost: a few cents per program review.

The model sits behind one small adapter so it can be swapped by config:

```
backend/app/llm/
  base.py             LLMClient protocol: generate(system, user, schema) -> parsed model
                      raises InvalidOutput (retry with feedback) or LLMUnavailable (fallback)
  openai_provider.py  Responses API: client.responses.parse(text_format=<pydantic model>),
                      reasoning effort "medium", timeout, max_retries, refusal / incomplete handling
  fake.py             scripted responses for tests and e2e (no network, no cost)
```

Config in `backend/.env` (never committed): `LLM_PROVIDER=openai`, `LLM_MODEL=gpt-6.1-sol`,
`OPENAI_API_KEY` (read as a secret, server-side only). Switching providers later means one new
adapter plus two `.env` lines, then a light prompt retune checked with the eval profiles.

Dependencies: `openai==3.36.0` (works with the already-pinned `httpx2` 2.13.1).

### OpenAI structured-output constraints that shape the schema

All fields required (optional fields become `X | None`). `additionalProperties: false`
everywhere. Exercise ids and muscle names are `enum`s of the library's values, so the model
can't invent one. Length limits are checked in our own validation, and violations become retry
feedback.

## Prompt design (prompt-engineering patterns)

| Pattern | Use |
|---|---|
| Persona | System prompt: an experienced, evidence-based strength coach reviewing a plan conservatively; changes only with a clear, lifter-specific reason |
| Context injection | Rules, exercise library, edit budget, volume band, baseline program and computed starting loads go in the prompt; the engine does the maths |
| Few-shot | 3 worked examples: (1) no edits needed, (2) injury swap with a good reason, (3) an experienced lifter whose background says high volume doesn't suit them gets *less* volume — experience doesn't automatically mean more |
| Structured output | Strict JSON schema (above) |
| Iterative refinement | Rejected edits are fed back with reasons (`refine.py`, max 3 attempts) |
| Prompt caching | Stable system prompt first; per-lifter content last (automatic caching ≥ 1,024 tokens) |

Untrusted input: injury notes and training background go in delimited
`<untrusted_lifter_notes>` blocks in the user message, never in the system prompt. They can
motivate edits but can never relax a guard. Feedback to the model names fields and rules, never
echoes model text.

Every prompt version is logged in `docs/prompt-log.md` with eval results.

## API and frontend

- `POST /program/review`: same response as `/program/template`, plus `source` (`ai` or
  `baseline`), `summary`, applied `edits` with reasons, and a generic `fallback_reason`. Error
  details stay in server logs.
- Abuse and cost limits: per-IP rate limit (5 reviews per 10 minutes) and a daily global cap
  (200), both configurable. Over the limit returns the baseline with a note. Also set a monthly
  spend limit in the OpenAI dashboard.
- Onboarding gains the optional "Training background" box and calls the review endpoint
  ("Your coach is reviewing your program…"). The Board shows a "Coach adjustments" card: the
  summary, each edit with its reason (e.g. "Quads 10 → 8 sets: you recover better on less"),
  labelled AI-written, and a note when the baseline was used instead.

## Testing

- Unit tests with the fake LLM: edit-budget rules, volume band, injury-only edits, guard feedback
  loop, refusal, timeout, malformed output, and adapter error mapping (mocked SDK). No API cost.
- Endpoint and e2e tests run with `LLM_PROVIDER=fake`, so they are deterministic and keyless.
- **Eval profiles** (`backend/evals/profiles.json`, about 10 lifters: no-injury beginner, shoulder,
  knee, lower back, dumbbells-only/45 min, kg units, 2- and 6-day, an experienced lifter who does
  better on low volume, vague injury text, a prompt-injection attempt) plus
  `scripts/run_eval.py`. It runs the real model and reports first-try pass rate, retries,
  fallbacks, injury and volume handling, cost and latency, and writes a blinded side-by-side
  page for human review. Manual (needs a key, costs cents). It doubles as a model bake-off if
  the model is ever reconsidered.

## Build order

1. Profile field + edit schema + edit validation/application (incl. volume edits), test-first
2. Provider adapter (fake + OpenAI) and the review service wired into `refine.py`
3. Prompt v1 + few-shot examples + `/program/review` endpoint + rate limits
4. Frontend: onboarding box, review call, Coach adjustments card
5. Eval profiles + script; prompt-log v1 entry
6. Three neutral reviews → fixes → PR → merge on approval

## Process

Feature branch → build test-first → three neutral reviewers (general, security, testing; whole
project, with before/after comparison) → fixes → PR via `gh` → merge on approval. `main` stays
demo-ready and is protected.

## Open items carried from the reviews

Before any public deployment: stream-count request bodies (chunked uploads bypass the
Content-Length cap), drop `input` from 422 details, disable `/docs`, add security headers.
Accepted: a slot never lifted is left for the lifter to pick; cable-only and machine-only
profiles with many days get a readable 422.
