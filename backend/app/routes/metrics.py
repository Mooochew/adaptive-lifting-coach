from fastapi import APIRouter

from app.schemas import Metrics, MetricsRequest
from app.training import compute_metrics

router = APIRouter(tags=["metrics"])


@router.post("/metrics", response_model=Metrics)
def metrics(req: MetricsRequest) -> Metrics:
    """Compute e1RM, volume, effort, plateaus, next steps and deload signals. No LLM involved."""
    return compute_metrics(req.goal, req.program, req.logs)
