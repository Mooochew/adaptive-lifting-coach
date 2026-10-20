from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.config import get_settings
from app.exercises import EXERCISES, ExerciseInfo
from app.routes import metrics
from app.rules import GOALS, SHARED, GoalRules, SharedRules

settings = get_settings()

app = FastAPI(
    title="Adaptive Lifting Coach API",
    description="Training math and AI coaching for the Adaptive Lifting Coach frontend.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(metrics.router)


class Health(BaseModel):
    ok: bool
    demo_mode: bool


class RulesResponse(BaseModel):
    goals: dict[str, GoalRules]
    shared: SharedRules


@app.get("/health", tags=["meta"], response_model=Health)
def health() -> Health:
    """Liveness check. The frontend also calls this to wake a sleeping free-tier server."""
    return Health(ok=True, demo_mode=settings.demo_mode)


@app.get("/exercises", tags=["meta"], response_model=list[ExerciseInfo])
def exercises() -> list[ExerciseInfo]:
    """The canonical exercise library."""
    return list(EXERCISES)


@app.get("/rules", tags=["meta"], response_model=RulesResponse)
def rules() -> RulesResponse:
    """Training rules per goal, plus shared thresholds."""
    return RulesResponse(goals=GOALS, shared=SHARED)
