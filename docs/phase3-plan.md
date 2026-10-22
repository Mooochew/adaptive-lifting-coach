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

## Step 3.2 — design: the engine writes the program, the AI edits it

AI-generated workout plans tend to be generic, too high in volume, and poorly grounded in
research. So the AI does **not** design programs. Instead:

1. The **rules engine** builds the baseline program (evidence-based, deterministic, already
   passes every guard). New: the baseline becomes **experience-aware** (below).
2. The **AI reviews** the baseline together with the lifter's profile (goal, experience,
   equipment, time, recent sets, injury notes) and returns a short list of **proposed edits**,
   each with a reason, plus a short coaching summary.
3. The engine checks every edit against an **edit budget**, applies the allowed ones to that
   slot in every week of the block, then runs the full guards again.
4. Edits that break a rule are sent back with the reason (Iterative Refinement, max 3 attempts).
   If the AI still can't produce valid edits, or is unavailable, the lifter gets the **baseline**.
   The lifter always gets a valid program.

The AI never sets weights. Starting loads come from the lifter's recent sets via the existing
formula, and the server sets units, block number and progression state.

### Edit budget

| Edit | Normal | Only when it cites an injury the lifter entered |
|---|---|---|
| Swap exercise | Only to an alternative the template already lists for that slot, with available equipment | Any library exercise with available equipment that shares a primary muscle |
| Sets | ±1 per exercise, within volume limits | May reduce further or remove the exercise |
| Rep range | Within the rules' range for that lift | Same |
| Target effort (RPE) | Within the allowed range | May go lower (easier) |
| Count | ≤ 4 edits; ≤ 1 edit per slot | Not counted toward the 4 |

"Injury" edits are rejected when the profile has no injury text. Each edit needs a reason
(≤ 200 characters). The summary is ≤ 400 characters. Both are shown in the UI labelled as
AI-written.

### Extra checks on the edited program

Every exercise uses available equipment; days per week unchanged; each session fits the time
budget; week-1 volume per muscle stays within the goal's starting range; then the existing
guards (session cap, weekly max, ramp, deload rules, loads).

### Experience-aware baseline (deterministic, before any AI)

Today experience is ignored. Proposed values (to confirm):

| | Beginner | Intermediate (today's behaviour) | Advanced |
|---|---|---|---|
| Starting weekly sets per muscle — hypertrophy (range 10–12) | 8 | 10 | 11 |
| Starting weekly sets per muscle — strength (range 6–10) | 6 | 6 | 8 |
| Target effort on main and compound lifts | 1 RPE easier (more reps in reserve, for technique) | unchanged | unchanged |
| Weekly ramp | unchanged | unchanged | unchanged |

Rationale: novices grow and get stronger on lower volumes, and trained lifters tend to need
more. Effort further from failure while technique is learned is standard practice. Exercise
selection is unchanged (barbell basics stay when the equipment is there).

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

Dependencies: `openai==3.36.0` (needs `httpx2` 2.16, upgraded from 2.13.1).

### OpenAI structured-output constraints that shape the schema

All fields required (optional fields become `X | None`). `additionalProperties: false`
everywhere. Exercise ids are an `enum` of the library's 30 ids, so the model can't invent one.
Length limits are checked in our own validation, and violations become retry feedback.

## Prompt design (prompt-engineering patterns)

| Pattern | Use |
|---|---|
| Persona | System prompt: evidence-based strength coach who reviews another coach's plan conservatively |
| Context injection | Rules, exercise library, edit budget, baseline program and computed starting loads go in the prompt; the engine does the maths |
| Few-shot | 2 worked examples: one with no edits needed, one injury swap with a good reason |
| Structured output | Strict JSON schema (above) |
| Iterative refinement | Rejected edits are fed back with reasons (`refine.py`, max 3 attempts) |
| Prompt caching | Stable system prompt first; per-lifter content last (automatic caching ≥ 1,024 tokens) |

Untrusted input: the injury text goes in a delimited `<untrusted_lifter_notes>` block in the
user message, never in the system prompt. It can motivate injury edits but can never relax a
guard. Feedback to the model names fields and rules, never echoes model text.

Every prompt version is logged in `docs/prompt-log.md` with eval results.

## API and frontend

- `POST /program/review`: same response as `/program/template`, plus `source` (`ai` or
  `baseline`), `summary`, applied `edits` with reasons, and a generic `fallback_reason`. Error
  details stay in server logs.
- Abuse and cost limits: per-IP rate limit (default 5 reviews per 10 minutes) and a daily
  global cap (default 200), both configurable. Over the limit returns the baseline with a note.
  Also set a monthly spend limit in the OpenAI dashboard.
- Onboarding calls the review endpoint ("Your coach is reviewing your program…"). The Board
  shows a "Coach adjustments" card: the summary, each edit with its reason, labelled AI-written,
  and a note when the baseline was used instead.

## Testing

- Unit tests with the fake LLM: edit-budget rules, injury-only edits, guard feedback loop, refusal,
  timeout, malformed output, and adapter error mapping (mocked SDK). No API cost.
- Endpoint and e2e tests run with `LLM_PROVIDER=fake`, so they are deterministic and keyless.
- **Eval profiles** (`backend/evals/profiles.json`, about 10 lifters: no-injury beginner, shoulder,
  knee, lower back, dumbbells-only/45 min, kg units, 2- and 6-day, vague injury text, a
  prompt-injection attempt) plus `scripts/run_eval.py`. It runs the real model and reports
  first-try pass rate, retries, fallbacks, injury handling, cost and latency, and writes a
  blinded side-by-side page for human review. Manual (needs a key, costs cents). It doubles as a
  model bake-off if the model is ever reconsidered.

## Process

Feature branch → build test-first → three neutral reviewers (general, security, testing; whole
project, with before/after comparison) → fixes → PR via `gh` → merge on approval. `main` stays
demo-ready and is protected.

## Open items carried from the reviews

Before any public deployment: stream-count request bodies (chunked uploads bypass the
Content-Length cap), drop `input` from 422 details, disable `/docs`, add security headers.
Accepted: a slot never lifted is left for the lifter to pick; cable-only and machine-only
profiles with many days get a readable 422.
