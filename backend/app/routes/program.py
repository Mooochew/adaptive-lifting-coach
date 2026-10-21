from fastapi import APIRouter, HTTPException

from app.demo import build_demo
from app.progression import advance_program
from app.schemas import AdvanceResponse, DemoAthlete, MetricsRequest, TemplateRequest, TemplateResponse
from app.templates import TemplateError, build_template

router = APIRouter(tags=["program"])


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
