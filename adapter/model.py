from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal, localcontext
import re
from zoneinfo import ZoneInfo


METRICS = (
    "active_power_w_avg", "phase1_power_w_avg", "phase2_power_w_avg",
    "phase3_power_w_avg", "reactive_power_var_avg", "apparent_power_va_avg",
    "current_avg_a", "voltage_avg_v", "frequency_hz_avg", "meter_temperature_avg",
)
DAILY_METRICS = (
    "active_power_w_avg", "reactive_power_var_avg", "apparent_power_va_avg",
    "voltage_avg_v", "frequency_hz_avg", "meter_temperature_avg",
)
HOURLY_COLUMNS = ("bucket_start", "sensor_name", "points_count", "active_power_w_max", *METRICS, "energy_kwh")
DAILY_COLUMNS = ("bucket_start", "sensor_name", "points_count", "active_power_w_max", *DAILY_METRICS, "energy_kwh")


def table_parts(value: str) -> tuple[str, str]:
    if not isinstance(value, str) or not re.fullmatch(r"[^\W\d]\w*\.[^\W\d]\w*", value):
        raise ValueError("Table names must be schema-qualified: schema.table")
    return tuple(value.split("."))


@dataclass(frozen=True)
class Settings:
    source_dsn: str = field(repr=False)
    target_dsn: str = field(repr=False)
    source_table: str = "public.electricity_sensor_readings"
    hourly_table: str = "student_schema.electricity_sensor_readings_hourly"
    daily_table: str = "student_schema.electricity_sensor_readings_daily"
    timezone: str = "Europe/Moscow"
    lookback_days: int = 2
    statement_timeout_seconds: int = 120
    sample_seconds: Decimal | None = None
    profile: dict | None = None

    def __post_init__(self):
        for value in (self.source_table, self.hourly_table, self.daily_table):
            table_parts(value)
        if self.hourly_table == self.daily_table:
            raise ValueError("Hourly and daily tables must be different")
        ZoneInfo(self.timezone)
        if self.lookback_days < 0 or self.statement_timeout_seconds <= 0:
            raise ValueError("Invalid lookback or statement timeout")
        if self.sample_seconds is not None and (
            not self.sample_seconds.is_finite() or self.sample_seconds <= 0
        ):
            raise ValueError("sample_seconds must be a positive finite number")


def midnight(day: date, timezone: str) -> datetime:
    return datetime.combine(day, time.min, ZoneInfo(timezone))


def plan_window(settings: Settings, now: datetime, start: date | None, end: date | None,
                watermark: datetime | None, apply: bool) -> tuple[datetime, datetime]:
    if now.tzinfo is None:
        raise ValueError("now must include a timezone")
    zone = ZoneInfo(settings.timezone)
    now = now.astimezone(zone)
    if watermark is not None:
        if watermark.tzinfo is None or watermark > now:
            raise ValueError("Invalid checkpoint time")
    if start is None:
        if apply and watermark is None:
            raise ValueError("The first --apply requires an explicit --from date")
        anchor = watermark.astimezone(zone).date() if watermark else now.date()
        start = anchor - timedelta(days=settings.lookback_days)
    # Explicit ranges are independent of automatic progress, including old backfills.
    finish = min(midnight(end, settings.timezone), midnight(now.date(), settings.timezone)) if end else midnight(now.date(), settings.timezone)
    begin = midnight(start, settings.timezone)
    if finish > now:
        raise ValueError("The end of the range must not be in the future")
    if end is not None and start >= end:
        raise ValueError("--from must be earlier than --to")
    if begin >= finish:
        return begin, begin
    return begin, finish


def day_windows(begin: datetime, end: datetime, timezone: str):
    zone = ZoneInfo(timezone)
    current = begin.astimezone(zone)
    if current != midnight(current.date(), timezone):
        raise ValueError("Refresh must start at a local day boundary")
    while current < end:
        stop = min(midnight(current.date() + timedelta(days=1), timezone), end)
        yield current, stop
        current = stop


def build_rows(stats: list[dict], settings: Settings) -> tuple[list[dict], list[dict]]:
    """Keep per-metric sums/counts until daily averages have been calculated."""
    hourly, daily = [], {}
    with localcontext() as context:
        context.prec = 50
        for item in stats:
            row = {key: item[key] for key in ("bucket_start", "sensor_name", "points_count", "active_power_w_max")}
            for metric in METRICS:
                count = item[metric + "_count"]
                row[metric] = item[metric + "_sum"] / count if count else None
            power_sum = item["active_power_w_avg_sum"]
            row["energy_kwh"] = (
                power_sum * settings.sample_seconds / Decimal(3600000)
                if settings.sample_seconds is not None and power_sum is not None else None
            )
            hourly.append(row)
            day = item["bucket_start"].astimezone(ZoneInfo(settings.timezone)).date()
            key = day, item["sensor_name"]
            if key not in daily:
                daily[key] = {"bucket_start": day, "sensor_name": item["sensor_name"], "points_count": 0,
                              "active_power_w_max": None, "energy_kwh": None,
                              "_energy_complete": True,
                              **{m + "_sum": Decimal(0) for m in DAILY_METRICS},
                              **{m + "_count": 0 for m in DAILY_METRICS}}
            total = daily[key]
            total["points_count"] += item["points_count"]
            maximum = item["active_power_w_max"]
            if maximum is not None:
                total["active_power_w_max"] = maximum if total["active_power_w_max"] is None else max(maximum, total["active_power_w_max"])
            for metric in DAILY_METRICS:
                total[metric + "_sum"] += item[metric + "_sum"] or Decimal(0)
                total[metric + "_count"] += item[metric + "_count"]
            if row["energy_kwh"] is not None:
                total["energy_kwh"] = (total["energy_kwh"] or Decimal(0)) + row["energy_kwh"]
            else:
                total['_energy_complete'] = False
        for total in daily.values():
            if not total.pop('_energy_complete'):
                total['energy_kwh'] = None
            for metric in DAILY_METRICS:
                count = total.pop(metric + "_count")
                summed = total.pop(metric + "_sum")
                total[metric] = summed / count if count else None
    return hourly, list(daily.values())
