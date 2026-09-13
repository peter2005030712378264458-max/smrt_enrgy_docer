from datetime import date

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas.period_comparison import PeriodComparisonRequest, PeriodInput, PeriodStats
from app.services.period_comparison import build_period_comparison_response


def make_period_stats(key, mean_kw):
    return PeriodStats(
        key=key,
        date_from=date(2021, 1, 1) if key == "period_1" else date(2021, 1, 3),
        date_to=date(2021, 1, 2) if key == "period_1" else date(2021, 1, 4),
        actual_from=None,
        actual_to=None,
        observations=25,
        source_points=100,
        mean_kw=mean_kw,
        stddev_kw=2,
        variance_kw2=4,
        total_energy_kwh=mean_kw * 25,
    )


def test_request_rejects_intersecting_periods():
    with pytest.raises(ValidationError):
        PeriodComparisonRequest(
            period_1={"date_from": "2021-01-01", "date_to": "2021-01-10"},
            period_2={"date_from": "2021-01-10", "date_to": "2021-01-15"},
        )


def test_request_rejects_period_with_reversed_dates():
    with pytest.raises(ValidationError):
        PeriodComparisonRequest(
            period_1={"date_from": "2021-01-10", "date_to": "2021-01-01"},
            period_2={"date_from": "2021-02-01", "date_to": "2021-02-02"},
        )


def test_build_period_comparison_response_calculates_z_statistic():
    request = PeriodComparisonRequest(
        period_1={"date_from": "2021-01-01", "date_to": "2021-01-02"},
        period_2={"date_from": "2021-01-03", "date_to": "2021-01-04"},
        alpha=0.05,
    )
    period_1 = make_period_stats("period_1", 10)
    period_2 = make_period_stats("period_2", 12)

    response = build_period_comparison_response(request, period_1, period_2)

    assert request.alternative == "two_sided"
    assert response.alternative == "two_sided"
    assert response.z_critical == 1.96
    assert response.z_statistic == pytest.approx(-3.5355, rel=1e-3)
    assert response.reject_null is True


def test_greater_alternative_rejects_only_positive_direction():
    request = PeriodComparisonRequest(
        period_1={"date_from": "2021-01-01", "date_to": "2021-01-02"},
        period_2={"date_from": "2021-01-03", "date_to": "2021-01-04"},
        alpha=0.05,
        alternative="greater",
    )

    response = build_period_comparison_response(request, make_period_stats("period_1", 12), make_period_stats("period_2", 10))

    assert response.alternative == "greater"
    assert response.alternative_hypothesis == "H1: среднее потребление первого периода больше второго"
    assert response.decision_rule == "z_statistic > z_critical"
    assert response.z_critical == 1.65
    assert response.z_statistic == pytest.approx(3.5355, rel=1e-3)
    assert response.reject_null is True
    assert "больше" in response.conclusion


def test_greater_alternative_does_not_reject_negative_direction():
    request = PeriodComparisonRequest(
        period_1={"date_from": "2021-01-01", "date_to": "2021-01-02"},
        period_2={"date_from": "2021-01-03", "date_to": "2021-01-04"},
        alpha=0.05,
        alternative="greater",
    )

    response = build_period_comparison_response(request, make_period_stats("period_1", 10), make_period_stats("period_2", 12))

    assert response.z_critical == 1.65
    assert response.z_statistic == pytest.approx(-3.5355, rel=1e-3)
    assert response.reject_null is False


def test_less_alternative_rejects_only_negative_direction():
    request = PeriodComparisonRequest(
        period_1={"date_from": "2021-01-01", "date_to": "2021-01-02"},
        period_2={"date_from": "2021-01-03", "date_to": "2021-01-04"},
        alpha=0.05,
        alternative="less",
    )

    response = build_period_comparison_response(request, make_period_stats("period_1", 10), make_period_stats("period_2", 12))

    assert response.alternative == "less"
    assert response.alternative_hypothesis == "H1: среднее потребление первого периода меньше второго"
    assert response.decision_rule == "z_statistic < -z_critical"
    assert response.z_critical == 1.65
    assert response.z_statistic == pytest.approx(-3.5355, rel=1e-3)
    assert response.reject_null is True
    assert "меньше" in response.conclusion


def test_build_period_comparison_response_requires_two_observations():
    request = PeriodComparisonRequest(
        period_1={"date_from": "2021-01-01", "date_to": "2021-01-02"},
        period_2={"date_from": "2021-01-03", "date_to": "2021-01-04"},
    )
    stats = PeriodStats(
        key="period_1",
        date_from=date(2021, 1, 1),
        date_to=date(2021, 1, 2),
        actual_from=None,
        actual_to=None,
        observations=1,
        source_points=1,
        mean_kw=1,
        stddev_kw=None,
        variance_kw2=None,
        total_energy_kwh=1,
    )

    with pytest.raises(HTTPException) as exc:
        build_period_comparison_response(request, stats, stats.model_copy(update={"key": "period_2"}))

    assert exc.value.status_code == 422


def test_health_endpoint():
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
