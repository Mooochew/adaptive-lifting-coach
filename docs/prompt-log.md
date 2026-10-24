# Prompt Log

Every version of the AI prompts, what changed, why, and how it scored on the eval set
(`backend/evals/profiles.json`, run with `python scripts/run_eval.py`).

## Program review prompt (`backend/app/prompts/program_review.py`)

The AI doesn't design programs. The rules engine writes an evidence-based plan, and the AI reviews
it like a coach, proposing a few bounded edits (each with a reason) that the engine validates and
applies. See `docs/phase3-plan.md`.

| Version | Date | Model | Change | Why | Eval result |
|---|---|---|---|---|---|
| v1 | 2026-10-21 | gpt-6.1-sol (medium effort) | First version (below) | — | Not run live. Scripted-model tests pass (prompt contents, caching prefix, untrusted-notes containment, retry feedback). |
| v2 | 2026-10-22 | gpt-6.1-sol (medium effort) | Injury edits no longer offer effort below the normal range; every session must keep an exercise; move volume a few sets at a time from the current number | Code review found the engine clamps effort back to the range (the card would have promised changes that didn't happen), and that large volume jumps often can't fit a schedule, which wastes paid retries | First live run, 2026-10-23, 1 run per case: accepted first try 8/10, fell back 1/10, checks met 9/10, 34.6k of 50.8k input tokens cached, 10.1 s per review, $0.077 total (≈$0.008 per review). The prompt-injection case held (no edits). Fallback: `experienced-low-volume` asked three times for volumes below what the schedule allows without dropping main lifts (e.g. upper back 10 when the floor is 13.5), even with the floor in the feedback. Coaching notes: the shoulder case removed the overhead press instead of swapping it; the lower-back case swapped deadlifts to leg curls *and* removed RDLs, leaving no hinge. |
| v3 | 2026-10-23 | gpt-6.1-sol (medium effort) | Each muscle's volume is shown with the lowest number the engine accepts in this schedule ("can't change" where nothing fits), replacing "move a few sets at a time"; injury edits prefer a swap that keeps the movement pattern and remove only when no listed option avoids the pain. Engine fix alongside: topping other muscles back up no longer pushes a lowered muscle back over its number, and a volume edit may land within a set of its number | v2's fallback was the engine, not the model: for the low-volume lifter every quad number landed half a set too high, so no answer could pass. Showing the floors up front saves paid retries. v2 removed the overhead press although the dumbbell press was a listed swap | 3 runs per case (30 reviews): accepted first try 24/30, fell back 1/30, checks met 29/30, 127k of 148k input tokens cached, 9.5 s per review, $0.170 total (≈$0.006 per review). Shoulder: swapped to the seated dumbbell press 3/3 (v2 removed it). Low-volume lifter: lowered chest, lats and quads to about 6 in 2/3; the third fell back because lowering chest and lats together pushed lats to 4, under the band (floors are computed one muscle at a time). Lower back: 2/3 swapped deadlifts to RDLs, which still hinge; the library has no back-friendly hip exercise. Standard programs unchanged (140 compared byte for byte). |
| v4 | 2026-10-24 | gpt-6.1-sol (medium effort) | Says the floors hold one muscle at a time and that muscles sharing exercises (rows: upper back and lats) pull each other down. Engine alongside: a clash between two lowered muscles is named in the retry feedback; a refill can't push an untouched muscle over the band's top; the library gains two back-friendly hip exercises for injury swaps (Barbell Hip Thrust, Cable Pull-Through; in no template) | v3's fallback was lats and upper back lowered together; v3's lower-back swaps had no hip exercise that spares the back, and refills after a deadlift swap pushed glutes over the band, costing a retry every time | 3 runs per case (30 reviews): accepted first try 29/30, fell back 0/30, checks met 30/30, 113k of 131k input tokens cached, 6.9 s per review, $0.133 total (≈$0.004 per review). Lower back: deadlift and RDL swapped to cable pull-throughs 3/3, first try. Low-volume lifter: chest, upper back and quads lowered 3/3 (one needed 3 attempts). Shoulder: dumbbell press 3/3. Standard programs unchanged (140 compared byte for byte). |

### v1: what's in it and which pattern each part uses

| Part | Pattern | Purpose |
|---|---|---|
| "experienced, evidence-based coach … reviewing a plan a rules engine wrote … make only changes that clearly fit this lifter" | **Persona** | Sets a conservative, research-grounded voice; frames the job as reviewing, not designing |
| Edit kinds, limits, volume bands (generated from `rules.py`), the full exercise library | **Context injection** | The model chooses; the engine does the maths. Bands and ids come from code, so they can't drift from what's enforced |
| Week 1 with each slot's sets, reps, RPE range, engine-computed load, allowed swaps; weekly sets per muscle | **Context injection** | Gives the model exactly the options it may use, which cuts down rejected edits |
| Three worked examples: no change needed; injury swap; an experienced lifter given *less* volume | **Few-shot** | Teaches that an empty edit list is fine and that experience alone doesn't mean more volume |
| Strict JSON schema (exercise ids and muscles as enums) | **Structured output** | The reply is always parseable and can't name an unknown exercise |
| Rejected edits returned in `<previous_attempt_problems>`, up to 3 attempts | **Iterative refinement** | The model fixes what broke the rules instead of the app silently dropping it |
| Lifter text only in `<lifter_notes>` (angle brackets neutralised), with an instruction to treat it as information | **Prompt-injection defence** | Notes can inform edits but can't override rules; the engine enforces the limits regardless |
| Identical system prompt for every lifter, first in the request | **Prompt caching** | Repeat requests reuse about 2,000 cached tokens (cheaper and faster) |

### How to add a version

1. Change the prompt and bump `PROMPT_VERSION`.
2. Run `python scripts/run_eval.py --runs 3` (needs the key; costs cents).
3. Add a row: what changed, why, and the eval numbers (first-try acceptance, fallbacks, checks met,
   cost), plus anything you noticed reading the summaries and reasons.
