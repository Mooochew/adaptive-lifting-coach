"""Run the AI program review over the eval set (evals/profiles.json) and write a report.

    python scripts/run_eval.py                 # real model from backend/.env (LLM_PROVIDER=openai + key)
    python scripts/run_eval.py --runs 3        # each case 3 times (replies vary)
    python scripts/run_eval.py --cases shoulder-overhead,prompt-injection
    python scripts/run_eval.py --fake          # scripted model: checks the script itself, costs nothing

Costs money with a real model (a few cents per case). Writes evals/results/<time>-<model>.md and .json
(gitignored). For each case it records: whether the review was accepted on the first try, attempts,
fallbacks, the automatic checks from profiles.json, tokens (and how many were cached), seconds, and an
estimated cost. Read the report's summaries and reasons yourself: the checks can't judge coaching.
Run the same cases on another model to compare (a bake-off); log prompt changes in docs/prompt-log.md.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.ai_review import ReviewResult, run_review  # noqa: E402
from app.config import Settings  # noqa: E402
from app.guards import weekly_muscle_sets  # noqa: E402
from app.llm import LLMClient, build_llm  # noqa: E402
from app.llm.fake import FakeLLM  # noqa: E402
from app.prompts.program_review import PROMPT_VERSION  # noqa: E402
from app.rules import VOLUME_TARGET_MUSCLES  # noqa: E402
from app.schemas import Profile  # noqa: E402
from app.templates import build_week_one  # noqa: E402

CASES = BACKEND / "evals" / "profiles.json"
RESULTS = BACKEND / "evals" / "results"

# USD per 1M tokens: (input, cached input, output). Check the provider's pricing page before relying on it.
PRICES = {"openai:gpt-6.1-sol": (2.00, 0.10, 10.00)}


def check(case: dict, profile: Profile, result: ReviewResult) -> dict[str, bool]:
    """The case's automatic expectations, each True (met) or False."""
    expect = case.get("expect", {})
    edits = result.outcome.applied
    baseline_week, _ = build_week_one(profile)
    final_week = result.outcome.program.weeks[0]
    checks: dict[str, bool] = {}

    if "max_edits" in expect:
        checks[f"at most {expect['max_edits']} edits"] = len(edits) <= expect["max_edits"]
    if expect.get("address"):
        # Every slot that held the exercise must have been changed, or the exercise must be gone.
        touched = {(e.day, e.slot_id) for e in edits if e.slot_id}
        remaining = {e.exercise_id for s in final_week.sessions for e in s.exercises}
        for exercise_id in expect["address"]:
            slots = {(s.day, e.slot_id) for s in baseline_week.sessions for e in s.exercises if e.exercise_id == exercise_id}
            checks[f"addresses {exercise_id}"] = exercise_id not in remaining or slots <= touched
    if expect.get("lower_volume_any"):
        before, after = weekly_muscle_sets(baseline_week), weekly_muscle_sets(final_week)
        checks["lowers some muscle's volume"] = any(after.get(m, 0) < before.get(m, 0) for m in VOLUME_TARGET_MUSCLES)
    if expect.get("no_removals"):
        checks["no exercises removed"] = not any(e.kind == "remove" for e in edits)
    return checks


def cost(name: str, usage: dict) -> float | None:
    if name not in PRICES:
        return None
    price_in, price_cached, price_out = PRICES[name]
    uncached = usage["input_tokens"] - usage["cached_tokens"]
    return (uncached * price_in + usage["cached_tokens"] * price_cached + usage["output_tokens"] * price_out) / 1e6


def run(llm: LLMClient, cases: list[dict], runs: int = 1) -> list[dict]:
    rows = []
    for case in cases:
        profile = Profile.model_validate(case["profile"])
        for n in range(1, runs + 1):
            before = dict(vars(llm.usage))
            before_seconds = llm.usage.seconds
            result = run_review(profile, llm)
            usage = {k: getattr(llm.usage, k) - before[k]
                     for k in ("calls", "input_tokens", "cached_tokens", "output_tokens", "reasoning_tokens")}
            checks = check(case, profile, result)
            rows.append({
                "case": case["id"], "run": n, "about": case.get("about", ""),
                "injuries": profile.injuries, "training_background": profile.training_background,
                "source": result.source, "attempts": result.attempts,
                "first_try": result.source == "ai" and result.attempts == 1,
                "fallback_reason": result.fallback_reason,
                "summary": result.outcome.summary if result.source == "ai" else "",
                "edits": [{"change": e.change, "reason": e.reason, "for_injury": e.for_injury}
                          for e in result.outcome.applied],
                "checks": checks, "passed": all(checks.values()),
                "usage": usage, "seconds": round(llm.usage.seconds - before_seconds, 1),
                "cost_usd": cost(llm.name, usage),
            })
    return rows


def report(rows: list[dict], model: str) -> str:
    n = len(rows)
    total_cost = sum(r["cost_usd"] or 0 for r in rows)
    lines = [
        f"# AI review eval: {model}, prompt {PROMPT_VERSION}",
        "",
        f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC · {n} runs",
        "",
        f"- Accepted on the first try: **{sum(r['first_try'] for r in rows)}/{n}**",
        f"- Fell back to the standard plan: **{sum(r['source'] == 'baseline' for r in rows)}/{n}**",
        f"- All automatic checks met: **{sum(r['passed'] for r in rows)}/{n}**",
        f"- Cached input tokens: {sum(r['usage']['cached_tokens'] for r in rows)} of "
        f"{sum(r['usage']['input_tokens'] for r in rows)}",
        f"- Seconds per review: {sum(r['seconds'] for r in rows) / max(n, 1):.1f} average",
        f"- Estimated cost: ${total_cost:.3f} total" if any(r["cost_usd"] is not None for r in rows)
        else "- Estimated cost: n/a (no price for this model)",
        "",
        "| Case | Run | Source | Attempts | Edits | Checks | Seconds | Cost |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        checks = "✓" if r["passed"] else "✗ " + ", ".join(k for k, ok in r["checks"].items() if not ok)
        price = f"${r['cost_usd']:.4f}" if r["cost_usd"] is not None else "–"
        lines.append(f"| {r['case']} | {r['run']} | {r['source']} | {r['attempts']} | {len(r['edits'])} | "
                     f"{checks} | {r['seconds']} | {price} |")
    for r in rows:
        lines += ["", f"## {r['case']} (run {r['run']})", "", f"_{r['about']}_", ""]
        if r["injuries"]:
            lines.append(f"- Injuries: {r['injuries']}")
        if r["training_background"]:
            lines.append(f"- Background: {r['training_background']}")
        if r["source"] == "baseline":
            lines.append(f"- **Standard plan used:** {r['fallback_reason']}")
        else:
            lines += [f"- **Summary:** {r['summary']}", "- **Edits:**" if r["edits"] else "- **Edits:** none"]
            lines += [f"  - {e['change']}{' (injury)' if e['for_injury'] else ''}: {e['reason']}" for e in r["edits"]]
        lines += [f"- Check: {'✓' if ok else '✗'} {name}" for name, ok in r["checks"].items()]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> Path:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--cases", help="comma-separated case ids (default: all)")
    parser.add_argument("--fake", action="store_true", help="use the scripted model (no key, no cost)")
    parser.add_argument("--out", type=Path, default=RESULTS)
    args = parser.parse_args(argv)

    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    if args.cases:
        wanted = set(args.cases.split(","))
        cases = [c for c in cases if c["id"] in wanted]
    llm = FakeLLM() if args.fake else build_llm(Settings())
    if llm is None:
        sys.exit("The AI review is off. Set LLM_PROVIDER=openai and OPENAI_API_KEY in backend/.env, or use --fake.")

    rows = run(llm, cases, args.runs)
    args.out.mkdir(parents=True, exist_ok=True)
    stem = f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{llm.name.replace(':', '-')}"
    (args.out / f"{stem}.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    path = args.out / f"{stem}.md"
    path.write_text(report(rows, llm.name), encoding="utf-8")
    print(report(rows, llm.name).split("\n## ")[0])
    print(f"Full report: {path}")
    return path


if __name__ == "__main__":
    main()
