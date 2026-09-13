from pathlib import Path

import pytest
from fastapi import HTTPException

from app.services.z_table import ZTable


def test_alpha_005_returns_196():
    lookup = ZTable().lookup_critical(0.05)

    assert lookup.matched_z == 1.96
    assert lookup.matched_cdf == 0.975


def test_one_sided_alpha_005_returns_165():
    lookup = ZTable().lookup_critical(0.05, two_sided=False)

    assert lookup.target_cdf == pytest.approx(0.95)
    assert lookup.matched_z == 1.65
    assert lookup.matched_cdf == 0.95053


def test_alpha_001_returns_329():
    lookup = ZTable().lookup_critical(0.001)

    assert lookup.matched_z == 3.29


def test_table_can_be_loaded_from_custom_csv(tmp_path: Path):
    table_path = tmp_path / "z.csv"
    table_path.write_text("z,area_0_to_z,cdf\n0.00,0.00000,0.50000\n1.96,0.47500,0.97500\n", encoding="utf-8")

    lookup = ZTable(table_path).lookup_critical(0.05)

    assert lookup.matched_z == 1.96


def test_alpha_out_of_range_raises_http_exception():
    try:
        ZTable().lookup_critical(1)
    except HTTPException as exc:
        assert exc.status_code == 422
    else:
        raise AssertionError("Expected HTTPException")
