from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Request

from app.ai_review import ReviewResponse, run_review, standard_plan
from app.config import get_settings
from app.demo import build_demo
from app.llm import LLMClient, build_llm
from app.progression import advance_program
from app.rate_limit import RateLimiter
from app.schemas import AdvanceResponse, DemoAthlete, MetricsRequest, TemplateRequest, TemplateResponse
from app.templates import TemplateError, build_template, build_week_one

router = APIRouter(tags=["program"])


@lru_cache
def get_llm() -> LLMClient | None:
    """The configured model (one client for the process), or None when the AI review is off."""
    return build_llm(get_settings())


@lru_cache
def get_limiter() -> RateLimiter:
    s = get_settings()
    return RateLimiter(per_window=s.ai_reviews_per_window, window_s=s.ai_review_window_s,
                       per_day=s.ai_reviews_per_day)


@router.post("/program/review", response_model=ReviewResponse)
def program_review(req: TemplateRequest, request: Request, llm: LLMClient | None = Depends(get_llm),
                   limiter: RateLimiter = Depends(get_limiter)) -> ReviewResponse:
    """The standard plan, reviewed by the AI coach: a few bounded edits with reasons.

    Always returns a valid program: the standard plan when the AI is off, over the usage
    limits, unavailable, or can't produce edits that pass the checks (`source: "baseline"`,
    with a `fallback_reason`). 422 with a readable `detail` when the profile can't be built."""
    try:
        build_week_one(req.profile)
    except TemplateError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    if llm is not None and not limiter.allow(request.client.host if request.client else "unknown"):
        result = standard_plan(req.profile, "You've reached the limit of AI reviews for now, so this is the "
                                            "standard plan. Try again later.", block=req.block)
    else:
        result = run_review(req.profile, llm, block=req.block)
    return ReviewResponse.of(result)


@router.post("/program/template", response_model=TemplateResponse)
def program_template(req: TemplateRequest) -> TemplateResponse:
    """Rule-based 4-week block + deload for this profile. No LLM; also the AI fallback.

    422 with a readable `detail` when the profile can't be built (e.g. bodyweight only)."""
    try:
        program, notes, shortfalls = build_template(req.profile, block=req.block)
    except TemplateError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    return TemplateResponse(program=program, notes=notes, volume_shortfalls=shortfalls)


@router.post("/program/advance", response_model=AdvanceResponse)
def program_advance(req: MetricsRequest) -> AdvanceResponse:
    """Once a week has ended, write the engine's suggested loads into the following weeks.

    Idempotent: returns the program unchanged (and no changes) if there's nothing new to apply.
    """
    program, changes, notes = advance_program(req.goal, req.program, req.logs)
    return AdvanceResponse(program=program, changes=changes, notes=notes)


@router.get("/demo", response_model=DemoAthlete)
def demo() -> DemoAthlete:
    """A demo athlete with a program and 2.5 weeks of logs."""
    return build_demo()
