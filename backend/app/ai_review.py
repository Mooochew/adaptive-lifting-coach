"""Run the AI program review: prompt -> model -> edit budget + guards -> retry -> baseline fallback.

    baseline (engine) --> prompt (app/prompts/program_review.py) --> model (app/llm)
         ^                                                             |
         |                                    ProgramReview (strict JSON)
         |                                                             v
    fallback <-- max attempts / outage <-- refine.py <-- review_program (app/review.py)
                                              |  violations go back to the model as feedback
                                              v
                                      accepted: edited program + edits + summary

The lifter always gets a valid program. Error details go to the server log, never the response.
"""

import logging
from typing import Literal

from pydantic import Field

from app.llm import LLMClient
from app.prompts import program_review as prompt
from app.refine import Checked, refine
from app.review import AppliedEdit, ProgramReview, ReviewOutcome, baseline_outcome, review_program
from app.schemas import Model, Profile, Program
from app.templates import build_week_one

log = logging.getLogger(__name__)


class ReviewResult(Model):
    source: Literal["ai", "baseline"]
    outcome: ReviewOutcome
    attempts: int                       # model calls that returned something (0 = AI not used)
    fallback_reason: str | None         # safe to show the lifter


class ReviewResponse(Model):
    """What POST /program/review returns: /program/template's fields plus the review."""
    program: Program
    notes: list[str]
    volume_shortfalls: list[str]
    source: Literal["ai", "baseline"]
    summary: str = Field(description="The AI coach's summary; empty for the standard plan")
    edits: list[AppliedEdit] = Field(description="Edits applied to the standard plan, each with the AI's reason")
    fallback_reason: str | None = Field(description="Why the standard plan was used instead (safe to show)")

    @classmethod
    def of(cls, result: ReviewResult) -> "ReviewResponse":
        o = result.outcome
        return cls(program=o.program, notes=o.notes, volume_shortfalls=o.shortfalls, source=result.source,
                   summary=o.summary if result.source == "ai" else "", edits=o.applied,
                   fallback_reason=result.fallback_reason)


def standard_plan(profile: Profile, reason: str, block: int = 1) -> ReviewResult:
    return ReviewResult(source="baseline", outcome=baseline_outcome(profile, block), attempts=0,
                        fallback_reason=reason)


def run_review(profile: Profile, llm: LLMClient | None, block: int = 1) -> ReviewResult:
    baseline = baseline_outcome(profile, block)
    if llm is None:
        return ReviewResult(source="baseline", outcome=baseline, attempts=0,
                            fallback_reason="The AI coach isn't set up, so this is the standard plan.")

    week_one, _ = build_week_one(profile)

    def generate(feedback: list[str]) -> ProgramReview:
        return llm.generate(system=prompt.SYSTEM_PROMPT, user=prompt.user_message(profile, week_one, feedback),
                            schema=ProgramReview)

    def check(review: ProgramReview) -> Checked[ReviewOutcome]:
        outcome = review_program(review, profile, block)
        return Checked(outcome, outcome.notes, outcome.violations)

    refined = refine(generate, check, lambda: baseline)
    if refined.used_fallback:
        log.warning("AI review fell back to the baseline (%s, prompt %s): %s; attempts: %s; error: %s",
                    llm.name, prompt.PROMPT_VERSION, refined.fallback_reason, refined.attempts, refined.error)
        reason = ("The AI coach is unavailable right now, so this is the standard plan."
                  if refined.error else "The AI coach's suggestions didn't pass the safety checks, so this is "
                                        "the standard plan.")
        return ReviewResult(source="baseline", outcome=baseline, attempts=len(refined.attempts),
                            fallback_reason=reason)
    return ReviewResult(source="ai", outcome=refined.value, attempts=len(refined.attempts), fallback_reason=None)
