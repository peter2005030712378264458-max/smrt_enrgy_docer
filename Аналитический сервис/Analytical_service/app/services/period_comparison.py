from __future__ import annotations

from datetime import date, datetime, time, timedelta
from math import sqrt
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from psycopg import DatabaseError

from app.core.config import Settings, get_settings
from app.db.postgres import get_connection
from app.schemas.period_comparison import PeriodComparisonRequest, PeriodComparisonResponse, PeriodInput, PeriodStats, ZTableLookup
from app.services.z_table import ZTable, get_z_table


HYPOTHESIS = "H0: среднее потребление в двух периодах одинаковое"
ALTERNATIVE_HYPOTHESES = {
    "two_sided": "H1: среднее потребление в двух периодах различается",
    "greater": "H1: среднее потребление первого периода больше второго",
    "less": "H1: среднее потребление первого периода меньше второго",
}
DECISION_RULES = {
    "two_sided": "abs(z_statistic) > z_critical",
    "greater": "z_statistic > z_critical",
    "less": "z_statistic < -z_critical",
}


def _rejects_null(alternative: str, z_statistic: float, z_critical: float) -> bool:
    if alternative == "two_sided":
        return abs(z_statistic) > z_critical
    if alternative == "greater":
        return z_statistic > z_critical
    return z_statistic < -z_critical


def _build_conclusion(alternative: str, reject_null: bool, alpha: float, *, zero_standard_error: bool = False) -> str:
    if zero_standard_error:
        return (
            f"Нет оснований отвергнуть нулевую гипотезу для выбранной альтернативной гипотезы: "
            f"средние равны, а стандартная ошибка равна нулю при alpha = {alpha:g}."
        )

    if alternative == "two_sided":
        if reject_null:
            return (
                f"Нулевая гипотеза отвергается: среднее потребление в выбранных периодах "
                f"статистически значимо различается при alpha = {alpha:g}."
            )
        return (
            f"Нет оснований отвергнуть нулевую гипотезу: статистически значимого различия "
            f"среднего потребления при alpha = {alpha:g} не обнаружено."
        )

    if alternative == "greater":
        if reject_null:
            return (
                f"Нулевая гипотеза отвергается: среднее потребление первого периода "
                f"статистически значимо больше второго при alpha = {alpha:g}."
            )
        return (
            f"Нет оснований отвергнуть нулевую гипотезу: не обнаружено статистически значимого "
            f"подтверждения, что среднее потребление первого периода больше второго при alpha = {alpha:g}."
        )

    if reject_null:
        return (
            f"Нулевая гипотеза отвергается: среднее потребление первого периода "
            f"статистически значимо меньше второго при alpha = {alpha:g}."
        )
    return (
        f"Нет оснований отвергнуть нулевую гипотезу: не обнаружено статистически значимого "
        f"подтверждения, что среднее потребление первого периода меньше второго при alpha = {alpha:g}."
    )


def _period_bounds(period: PeriodInput, timezone_name: str) -> tuple[datetime, datetime]:
    timezone = ZoneInfo(timezone_name)
    start_at = datetime.combine(period.date_from, time.min, tzinfo=timezone)
    end_at = datetime.combine(period.date_to + timedelta(days=1), time.min, tzinfo=timezone)
    return start_at, end_at


def _row_to_period_stats(key: str, period: PeriodInput, row: dict) -> PeriodStats:
    return PeriodStats(
        key=key,
        date_from=period.date_from,
        date_to=period.date_to,
        actual_from=row.get("actual_from"),
        actual_to=row.get("actual_to"),
        observations=int(row.get("observations") or 0),
        source_points=int(row.get("source_points") or 0),
        mean_kw=_to_float(row.get("mean_kw")),
        stddev_kw=_to_float(row.get("stddev_kw")),
        variance_kw2=_to_float(row.get("variance_kw2")),
        total_energy_kwh=float(row.get("total_energy_kwh") or 0),
    )


def _to_float(value) -> float | None:
    if value is None:
        return None
    return float(value)


def fetch_period_stats(
    period: PeriodInput,
    key: str,
    data_names: list[str] | None,
    settings: Settings | None = None,
) -> PeriodStats:
    settings = settings or get_settings()
    start_at, end_at = _period_bounds(period, settings.timezone_name)
    data_filter = ""
    params = {
        "from_ts": start_at.isoformat(),
        "to_ts_exclusive": end_at.isoformat(),
    }

    if data_names is not None:
        data_filter = "AND a.sensor_name::text = ANY(%(data_names)s)"
        params["data_names"] = data_names

    sql = f"""
        WITH hourly AS (
            SELECT
                a.bucket_start,
                SUM(a.active_power_w_avg) / 1000.0 AS hourly_total_kw,
                SUM(a.active_power_w_avg) / 1000.0 AS hourly_energy_kwh,
                SUM(a.points_count) AS source_points
            FROM {settings.hourly_table} a
            WHERE a.bucket_start >= %(from_ts)s::timestamptz
              AND a.bucket_start < %(to_ts_exclusive)s::timestamptz
              {data_filter}
            GROUP BY a.bucket_start
        )
        SELECT
            COUNT(*) AS observations,
            COALESCE(SUM(source_points), 0) AS source_points,
            AVG(hourly_total_kw) AS mean_kw,
            STDDEV_SAMP(hourly_total_kw) AS stddev_kw,
            VAR_SAMP(hourly_total_kw) AS variance_kw2,
            COALESCE(SUM(hourly_energy_kwh), 0) AS total_energy_kwh,
            MIN(bucket_start) AS actual_from,
            MAX(bucket_start) AS actual_to
        FROM hourly
    """

    try:
        with get_connection(settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                row = cursor.fetchone() or {}
    except DatabaseError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Ошибка подключения к PostgreSQL при расчете аналитики",
        ) from exc

    return _row_to_period_stats(key, period, row)


def build_period_comparison_response(
    request: PeriodComparisonRequest,
    period_1: PeriodStats,
    period_2: PeriodStats,
    z_table: ZTable | None = None,
) -> PeriodComparisonResponse:
    lookup = (z_table or get_z_table()).lookup_critical(request.alpha, two_sided=request.alternative == "two_sided")
    z_critical = lookup.matched_z

    for stats in (period_1, period_2):
        if stats.observations < 2 or stats.stddev_kw is None or stats.mean_kw is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Для z-теста нужно минимум 2 часовых наблюдения в каждом периоде",
            )

    standard_error = sqrt((period_1.stddev_kw**2 / period_1.observations) + (period_2.stddev_kw**2 / period_2.observations))
    difference = period_1.mean_kw - period_2.mean_kw

    if standard_error == 0:
        if difference == 0:
            z_statistic = 0.0
            reject_null = False
            conclusion = _build_conclusion(request.alternative, reject_null, request.alpha, zero_standard_error=True)
        else:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Z-статистика не определена при нулевой стандартной ошибке и разных средних",
            )
    else:
        z_statistic = difference / standard_error
        reject_null = _rejects_null(request.alternative, z_statistic, z_critical)
        conclusion = _build_conclusion(request.alternative, reject_null, request.alpha)

    return PeriodComparisonResponse(
        hypothesis=HYPOTHESIS,
        alternative=request.alternative,
        alternative_hypothesis=ALTERNATIVE_HYPOTHESES[request.alternative],
        decision_rule=DECISION_RULES[request.alternative],
        alpha=request.alpha,
        metric=request.metric,
        unit="kW",
        periods=[period_1, period_2],
        difference_mean_kw=difference,
        standard_error=standard_error,
        z_statistic=z_statistic,
        z_critical=z_critical,
        reject_null=reject_null,
        conclusion=conclusion,
        table_lookup=ZTableLookup(
            source=lookup.source,
            target_cdf=lookup.target_cdf,
            matched_z=lookup.matched_z,
            matched_cdf=lookup.matched_cdf,
            alpha=lookup.alpha,
        ),
    )


def compare_periods(
    request: PeriodComparisonRequest,
    settings: Settings | None = None,
    z_table: ZTable | None = None,
) -> PeriodComparisonResponse:
    settings = settings or get_settings()
    period_1 = fetch_period_stats(request.period_1, "period_1", request.data_names, settings)
    period_2 = fetch_period_stats(request.period_2, "period_2", request.data_names, settings)
    return build_period_comparison_response(request, period_1, period_2, z_table)
