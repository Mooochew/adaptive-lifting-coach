from fastapi import APIRouter

from app.demo import build_demo
from app.schemas import DemoAthlete, Profile, TemplateResponse
from app.templates import build_template

router = APIRouter(tags=["program"])


@router.post("/program/template", response_model=TemplateResponse)
def program_template(profile: Profile) -> TemplateResponse:
    """Rule-based 4-week block + deload for this profile. No LLM; also the AI fallback."""
    program, notes = build_template(profile)
    return TemplateResponse(program=program, notes=notes)


@router.get("/demo", response_model=DemoAthlete)
def demo() -> DemoAthlete:
    """A demo athlete with a program and 2.5 weeks of logs."""
    return build_demo()
