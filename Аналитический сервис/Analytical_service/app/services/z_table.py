from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException, status


Z_TABLE_SOURCE = "NIST Engineering Statistics Handbook standard normal table"
DEFAULT_TABLE_PATH = Path(__file__).resolve().parent.parent / "data" / "standard_normal_cdf.csv"


@dataclass(frozen=True)
class ZTableRow:
    z: float
    area_0_to_z: float
    cdf: float


@dataclass(frozen=True)
class ZCriticalLookup:
    source: str
    target_cdf: float
    matched_z: float
    matched_cdf: float
    alpha: float


class ZTable:
    def __init__(self, path: Path = DEFAULT_TABLE_PATH):
        self.path = path
        self.rows = self._load_rows(path)

    @staticmethod
    def _load_rows(path: Path) -> list[ZTableRow]:
        rows = []
        with path.open("r", encoding="utf-8", newline="") as file:
            reader = csv.DictReader(file)
            for row in reader:
                rows.append(
                    ZTableRow(
                        z=float(row["z"]),
                        area_0_to_z=float(row["area_0_to_z"]),
                        cdf=float(row["cdf"]),
                    )
                )

        if not rows:
            raise ValueError("Z table is empty")
        return rows

    def lookup_critical(self, alpha: float, *, two_sided: bool = True) -> ZCriticalLookup:
        if not 0 < alpha < 1:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="alpha must be between 0 and 1")

        target_cdf = 1 - alpha / 2 if two_sided else 1 - alpha
        for row in self.rows:
            if row.cdf >= target_cdf:
                return ZCriticalLookup(
                    source=Z_TABLE_SOURCE,
                    target_cdf=target_cdf,
                    matched_z=row.z,
                    matched_cdf=row.cdf,
                    alpha=alpha,
                )

        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Уровень значимости слишком мал для доступной таблицы z-распределения",
        )


@lru_cache
def get_z_table() -> ZTable:
    return ZTable()
