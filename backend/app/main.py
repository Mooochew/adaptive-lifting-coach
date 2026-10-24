import math

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.config import get_settings
from app.exercises import EXERCISES, ExerciseInfo
from app.routes import coach, metrics, program
from app.rules import GOALS, SHARED, GoalRules, SharedRules

settings = get_settings()

# MAX_LOGS sets are ~4 MB of JSON; anything far bigger isn't a real request.
MAX_BODY_BYTES = 8 * 1024 * 1024

app = FastAPI(
    title="Adaptive Lifting Coach API",
    description="Training math and AI coaching for the Adaptive Lifting Coach frontend.",
    version="0.1.0",
)


@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    """Reject oversized requests before reading them (by Content-Length; a hosting proxy
    should also cap bodies sent without one). Registered before CORS so CORS wraps it
    and the frontend can read the 413."""
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
        return JSONResponse({"detail": f"Request too large (max {MAX_BODY_BYTES // (1024 * 1024)} MB)."},
                            status_code=413)
    return await call_next(request)


def _json_safe(value):
    """NaN / Infinity can't be written as JSON: show them as text in error messages."""
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """FastAPI's default 422, except it can't crash (500) when the bad input was NaN."""
    return JSONResponse({"detail": _json_safe(jsonable_encoder(exc.errors()))}, status_code=422)


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(metrics.router)
app.include_router(program.router)
app.include_router(coach.router)


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
