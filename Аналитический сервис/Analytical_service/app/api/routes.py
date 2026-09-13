from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.config import get_settings
from app.core.security import verify_internal_token
from app.schemas.period_comparison import PeriodComparisonRequest, PeriodComparisonResponse
from app.services.period_comparison import compare_periods


router = APIRouter()


@router.get("/health")
def health():
    return {"status": "ok", "service": get_settings().service_name}


@router.post(
    "/internal/analytics/period-comparison",
    response_model=PeriodComparisonResponse,
    dependencies=[Depends(verify_internal_token)],
)
def period_comparison(payload: PeriodComparisonRequest):
    return compare_periods(payload)
