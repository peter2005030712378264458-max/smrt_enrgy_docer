from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


AlternativeHypothesis = Literal["two_sided", "greater", "less"]


class PeriodInput(BaseModel):
    date_from: date
    date_to: date

    @model_validator(mode="after")
    def validate_order(self):
        if self.date_from > self.date_to:
            raise ValueError("Дата начала периода не может быть позже даты конца")
        return self


class PeriodComparisonRequest(BaseModel):
    period_1: PeriodInput
    period_2: PeriodInput
    alpha: float = Field(default=0.05, gt=0, lt=1)
    alternative: AlternativeHypothesis = "two_sided"
    data_names: list[str] | None = None
    metric: Literal["active_power_w_avg"] = "active_power_w_avg"

    @field_validator("data_names")
    @classmethod
    def normalize_data_names(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None

        result = []
        seen = set()
        for item in value:
            item = item.strip()
            if item and item not in seen:
                result.append(item)
                seen.add(item)
        return result

    @model_validator(mode="after")
    def validate_non_overlapping(self):
        non_overlapping = self.period_1.date_to < self.period_2.date_from or self.period_2.date_to < self.period_1.date_from
        if not non_overlapping:
            raise ValueError("Периоды не должны пересекаться")
        return self


class PeriodStats(BaseModel):
    key: Literal["period_1", "period_2"]
    date_from: date
    date_to: date
    actual_from: datetime | None
    actual_to: datetime | None
    observations: int
    source_points: int
    mean_kw: float | None
    stddev_kw: float | None
    variance_kw2: float | None
    total_energy_kwh: float


class ZTableLookup(BaseModel):
    source: str
    target_cdf: float
    matched_z: float
    matched_cdf: float
    alpha: float


class PeriodComparisonResponse(BaseModel):
    hypothesis: str
    alternative: AlternativeHypothesis
    alternative_hypothesis: str
    decision_rule: str
    alpha: float
    metric: Literal["active_power_w_avg"]
    unit: Literal["kW"]
    periods: list[PeriodStats]
    difference_mean_kw: float | None
    standard_error: float | None
    z_statistic: float | None
    z_critical: float
    reject_null: bool | None
    conclusion: str
    table_lookup: ZTableLookup
