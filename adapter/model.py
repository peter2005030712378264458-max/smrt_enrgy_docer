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
HOURLY_COLUMNS = ("bucket_start", "sensor_name", "points_count", "active_power_w_max", *METRICS,
                  *(metric + '_count' for metric in METRICS), "energy_kwh", "available_energy_kwh")
DAILY_COLUMNS = ("bucket_start", "sensor_name", "points_count", "active_power_w_max", *DAILY_METRICS,
                 *(metric + '_count' for metric in DAILY_METRICS), "energy_kwh", "available_energy_kwh")


def table_parts(value: str) -> tuple[str, str]:
    identifier = r'(?:"(?:[^"\x00]|"")+"|[^\W\d][\w$]*)'
    match = re.fullmatch(rf'\s*({identifier})\s*\.\s*({identifier})\s*', value) if isinstance(value, str) else None
    if not match:
        raise ValueError('Укажите таблицу в формате схема.таблица, например public.electricity_sensor_readings')
    return tuple(part[1:-1].replace('""', '"') if part.startswith('"') else part for part in match.groups())


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
        if table_parts(self.hourly_table) == table_parts(self.daily_table):
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


def fill_missing_hours(stats, sensors, begin, finish):
    """Materialize aggregate placeholders, never invented source measurements."""
    by_key = {(row['bucket_start'], row['sensor_name']): row for row in stats}
    current = begin
    output = []
    while current < finish:
        for sensor in sorted(sensors):
            output.append(by_key.get((current, sensor), {
                'bucket_start': current, 'sensor_name': sensor, 'points_count': 0,
                'active_power_w_max': None, 'power_seconds_sum': None,
                'power_seconds_count': 0,
                **{m + '_sum': None for m in METRICS},
                **{m + '_count': 0 for m in METRICS},
            }))
        current += timedelta(hours=1)
    return output


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
                row[metric + '_count'] = count
            power_seconds = item.get('power_seconds_sum')
            seconds = item.get('power_seconds_count', 0) or 0
            row['available_energy_kwh'] = (
                power_seconds / Decimal(3600000)
                if settings.sample_seconds is not None and power_seconds is not None
                and item['points_count'] > 0 else None)
            row['energy_kwh'] = row['available_energy_kwh'] if seconds == 3600 else None
            hourly.append(row)
            day = item["bucket_start"].astimezone(ZoneInfo(settings.timezone)).date()
            key = day, item["sensor_name"]
            if key not in daily:
                daily[key] = {"bucket_start": day, "sensor_name": item["sensor_name"], "points_count": 0,
                              "active_power_w_max": None, "energy_kwh": None,
                              'available_energy_kwh': None, '_hours': 0,
                              "_energy_complete": True,
                              **{m + "_sum": Decimal(0) for m in DAILY_METRICS},
                              **{m + "_count": 0 for m in DAILY_METRICS}}
            total = daily[key]
            total['_hours'] += 1
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
            if row['available_energy_kwh'] is not None:
                total['available_energy_kwh'] = (total['available_energy_kwh'] or Decimal(0)) + row['available_energy_kwh']
        for total in daily.values():
            hours = total.pop('_hours')
            if not total.pop('_energy_complete') or hours != 24:
                total['energy_kwh'] = None
            for metric in DAILY_METRICS:
                count = total[metric + "_count"]
                summed = total.pop(metric + "_sum")
                total[metric] = summed / count if count else None
    return hourly, list(daily.values())
