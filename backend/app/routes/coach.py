import threading
from functools import lru_cache

from fastapi import APIRouter, Depends, Request

from app.coach_notes import CoachNotesRequest, CoachNotesResponse, has_working_sets, no_note, run_notes
from app.config import get_settings
from app.llm import LLMClient
from app.rate_limit import RateLimiter
from app.routes.program import client_key, get_llm, get_review_slots

router = APIRouter(tags=["coach"])


@lru_cache
def get_notes_limiter() -> RateLimiter:
    """Coach notes have their own budget, so a few refreshes can't use up the AI reviews."""
    s = get_settings()
    return RateLimiter(per_window=s.ai_reviews_per_window, window_s=s.ai_review_window_s,
                       per_day=s.ai_reviews_per_day)


@router.post("/coach/notes", response_model=CoachNotesResponse)
def coach_notes(req: CoachNotesRequest, request: Request, llm: LLMClient | None = Depends(get_llm),
                limiter: RateLimiter = Depends(get_notes_limiter),
                slots: threading.BoundedSemaphore = Depends(get_review_slots)) -> CoachNotesResponse:
    """A short AI note about the latest logged week, grounded in the engine's numbers.

    `source: "none"` with a `reason` (safe to show) when nothing is logged, the AI is off, busy,
    over the usage limits, unavailable, or couldn't write a note that matches the numbers."""
    if llm is None or not has_working_sets(req.program, req.logs):   # no model call: no slot or quota used
        return run_notes(req.profile, req.program, req.logs, req.changes, llm, week=req.week)
    if not slots.acquire(blocking=False):
        return no_note("The AI coach is busy right now. Try again in a minute.", retryable=True)
    try:
        if not limiter.allow(client_key(request)):
            return no_note("You've reached the limit of coach notes for now. Try again later.", retryable=True)
        return run_notes(req.profile, req.program, req.logs, req.changes, llm,
                         deadline_s=get_settings().llm_review_deadline_s, week=req.week)
    finally:
        slots.release()
