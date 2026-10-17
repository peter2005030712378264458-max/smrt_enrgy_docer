from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from app.core.config import Settings
from app.schemas.period_comparison import PeriodInput
from app.services.period_comparison import fetch_period_stats


def test_saved_energy_null_is_preserved_and_power_statistics_are_unchanged():
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = {'observations': 2, 'source_points': 720, 'mean_kw': Decimal(2),
                                  'stddev_kw': Decimal(1), 'variance_kw2': Decimal(1), 'total_energy_kwh': None}
    settings = Settings(ENERGY_USE_STORED_ENERGY=True)
    with patch('app.services.period_comparison.get_connection') as get_connection:
        get_connection.return_value.__enter__.return_value = connection
        result = fetch_period_stats(PeriodInput(date_from=date(2026, 10, 15), date_to=date(2026, 10, 15)), 'period_1', None, settings)
    query = cursor.execute.call_args.args[0]
    assert 'SUM(a.energy_kwh)' in query
    assert 'COUNT(a.energy_kwh)' in query
    assert 'SUM(a.active_power_w_avg) / 1000.0 AS hourly_total_kw' in query
    assert result.total_energy_kwh is None
    assert result.mean_kw == 2
