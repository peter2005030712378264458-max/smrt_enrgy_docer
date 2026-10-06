from unittest.mock import MagicMock, patch
from types import SimpleNamespace

from django.test import SimpleTestCase
from rest_framework.test import APIClient

from .analytics_client import AnalyticsServiceUnavailable
from . import dashboard_queries


class DummyUser:
    is_active = True
    is_authenticated = True


class WeekdayHeatmapViewTests(SimpleTestCase):
    endpoint = "/api/consumption/dashboard/weekday-heatmap/"

    def setUp(self):
        self.client = APIClient()

    def test_requires_authentication(self):
        self.assertEqual(self.client.get(self.endpoint).status_code, 401)

    @patch("apps.consumption.dashboard_queries.get_weekday_heatmap")
    def test_pagination_validation(self, query):
        self.client.force_authenticate(user=DummyUser())
        for params in ({"page": 0}, {"page": "bad"}, {"page_size": 0}, {"page_size": 33}):
            with self.subTest(params=params):
                self.assertEqual(self.client.get(self.endpoint, params).status_code, 400)
        query.assert_not_called()

    @patch("apps.consumption.dashboard_queries.get_weekday_heatmap")
    def test_paginated_report_preserves_filters(self, query):
        self.client.force_authenticate(user=DummyUser())
        query.return_value = {"meters": [], "count": 0}
        response = self.client.get(self.endpoint, {"page": 2, "room": "101"})
        self.assertEqual(response.status_code, 200)
        request = query.call_args.args[0]
        self.assertEqual(request.query_params["room"], "101")
        self.assertEqual(query.call_args.kwargs, {"page": 2, "page_size": 8})


class WeekdayHeatmapQueryTests(SimpleTestCase):
    def run_report(self, *, granularity="day", page=1, meters=None, values=None, matching=None, count=17):
        metadata = MagicMock()
        analytics = MagicMock()
        metadata.execute.return_value.fetchone.return_value = {"count": count}
        metadata.execute.return_value.fetchall.return_value = meters if meters is not None else [
            {"data_name": "meter-1", "label": "Meter 1", "location": "101"},
        ]
        analytics.execute.return_value.fetchone.return_value = {"min_energy_kwh": 0, "max_energy_kwh": 240}
        analytics.execute.return_value.fetchall.return_value = values if values is not None else [
            {"data_name": "meter-1", "weekday": 1, "energy_kwh": 48},
            {"data_name": "meter-1", "weekday": 7, "energy_kwh": 0},
        ]
        request = SimpleNamespace(query_params={
            "granularity": granularity, "from": "2021-01-01T00:00:00+03:00",
            "to": "2021-01-07T23:59:59+03:00",
        })
        with patch.object(dashboard_queries, "dashboard_connection") as connection, patch.object(
            dashboard_queries, "_matching_data_names", return_value=matching,
        ):
            connection.return_value.__enter__.side_effect = [metadata, analytics]
            result = dashboard_queries.get_weekday_heatmap(request, page=page)
        return result, metadata, analytics

    def test_missing_days_differ_from_zero_and_scale_is_global(self):
        result, _, analytics = self.run_report()
        days = result["meters"][0]["days"]
        self.assertEqual([day["weekday"] for day in days], list(range(1, 8)))
        self.assertEqual(days[0]["energy_kwh"], 48)
        self.assertIsNone(days[1]["energy_kwh"])
        self.assertEqual(days[6]["energy_kwh"], 0)
        self.assertEqual(result["max_energy_kwh"], 240)
        self.assertNotIn("meter-1", analytics.execute.call_args_list[0].args[1])

    def test_day_and_week_use_daily_energy_and_date_filters(self):
        for granularity in ("day", "week"):
            with self.subTest(granularity=granularity):
                _, _, analytics = self.run_report(granularity=granularity)
                sql, params = analytics.execute.call_args.args
                self.assertIn(dashboard_queries._daily_table(), sql)
                self.assertIn("SUM(a.active_power_w_avg * 24.0) / 1000.0", sql)
                self.assertIn("ISODOW FROM a.bucket_start::date", sql)
                self.assertIn("LEFT(%s, 10)::date", sql)
                self.assertEqual(params[0], "meter-1")

    def test_hourly_energy_and_moscow_weekdays(self):
        _, _, analytics = self.run_report(granularity="hour")
        sql, params = analytics.execute.call_args.args
        self.assertIn(dashboard_queries._hourly_table(), sql)
        self.assertIn("SUM(a.active_power_w_avg) / 1000.0", sql)
        self.assertIn("AT TIME ZONE 'Europe/Moscow'", sql)
        self.assertIn("a.bucket_start::timestamptz >= %s::timestamptz", sql)
        self.assertEqual(len(params), 3)

    def test_page_changes_directory_offset_and_only_queries_visible_meters(self):
        meters = [{"data_name": "meter-9", "label": "Meter 9", "location": None}]
        result, metadata, analytics = self.run_report(page=2, meters=meters, values=[])
        sql, params = metadata.execute.call_args.args
        self.assertIn("ORDER BY data_name LIMIT %s OFFSET %s", sql)
        self.assertEqual(params, [8, 8])
        self.assertEqual(analytics.execute.call_args.args[1][0], "meter-9")
        self.assertEqual(result["total_pages"], 3)
        self.assertEqual(result["page"], 2)
        self.assertTrue(all(day["energy_kwh"] is None for day in result["meters"][0]["days"]))

    def test_directory_and_scale_apply_existing_meter_filters(self):
        _, metadata, analytics = self.run_report(matching=["meter-1", "meter-2"])
        self.assertEqual(metadata.execute.call_args.args[1], ["meter-1", "meter-2", 8, 0])
        self.assertEqual(analytics.execute.call_args_list[0].args[1][:2], ["meter-1", "meter-2"])

    def test_empty_directory_skips_analytics(self):
        result, _, analytics = self.run_report(meters=[], matching=[], count=0)
        self.assertEqual(result["meters"], [])
        self.assertEqual(result["total_pages"], 0)
        analytics.execute.assert_not_called()


class PeriodComparisonViewTests(SimpleTestCase):
    endpoint = "/api/consumption/analytics/period-comparison/"

    def setUp(self):
        self.client = APIClient()
        self.user = DummyUser()

    def authenticate(self):
        self.client.force_authenticate(user=self.user)

    def test_unauthenticated_request_returns_401(self):
        response = self.client.get(self.endpoint)

        self.assertEqual(response.status_code, 401)

    def test_missing_dates_return_400(self):
        self.authenticate()

        response = self.client.get(self.endpoint)

        self.assertEqual(response.status_code, 400)

    def test_intersecting_periods_return_400(self):
        self.authenticate()

        response = self.client.get(
            self.endpoint,
            {
                "period1_from": "2021-01-01",
                "period1_to": "2021-01-10",
                "period2_from": "2021-01-10",
                "period2_to": "2021-01-15",
            },
        )

        self.assertEqual(response.status_code, 400)

    def test_invalid_alpha_returns_400(self):
        self.authenticate()

        response = self.client.get(
            self.endpoint,
            {
                "period1_from": "2021-01-01",
                "period1_to": "2021-01-02",
                "period2_from": "2021-01-03",
                "period2_to": "2021-01-04",
                "alpha": "1.2",
            },
        )

        self.assertEqual(response.status_code, 400)

    def test_invalid_alternative_returns_400(self):
        self.authenticate()

        response = self.client.get(
            self.endpoint,
            {
                "period1_from": "2021-01-01",
                "period1_to": "2021-01-02",
                "period2_from": "2021-01-03",
                "period2_to": "2021-01-04",
                "alternative": "bad_value",
            },
        )

        self.assertEqual(response.status_code, 400)

    @patch("apps.consumption.analytics_views.request_period_comparison")
    @patch("apps.consumption.analytics_views.dashboard_queries._matching_data_names")
    @patch("apps.consumption.analytics_views.dashboard_connection")
    def test_successful_request_calls_analytics_service(self, dashboard_connection, matching_data_names, request_period_comparison):
        self.authenticate()
        dashboard_connection.return_value.__enter__.return_value = object()
        matching_data_names.return_value = ["meter-1", "meter-2"]
        request_period_comparison.return_value = {
            "hypothesis": "H0",
            "alternative": "greater",
            "alternative_hypothesis": "H1",
            "decision_rule": "z_statistic > z_critical",
            "alpha": 0.05,
            "metric": "active_power_w_avg",
            "unit": "kW",
            "periods": [],
            "difference_mean_kw": None,
            "standard_error": None,
            "z_statistic": None,
            "z_critical": 1.96,
            "reject_null": None,
            "conclusion": "ok",
            "table_lookup": {},
        }

        response = self.client.get(
            self.endpoint,
            {
                "period1_from": "2021-01-01",
                "period1_to": "2021-01-02",
                "period2_from": "2021-01-03",
                "period2_to": "2021-01-04",
                "alpha": "0.05",
                "alternative": "greater",
            },
        )

        self.assertEqual(response.status_code, 200)
        request_period_comparison.assert_called_once_with(
            {
                "period_1": {"date_from": "2021-01-01", "date_to": "2021-01-02"},
                "period_2": {"date_from": "2021-01-03", "date_to": "2021-01-04"},
                "alpha": 0.05,
                "alternative": "greater",
                "data_names": ["meter-1", "meter-2"],
                "metric": "active_power_w_avg",
            }
        )

    @patch("apps.consumption.analytics_views.request_period_comparison")
    @patch("apps.consumption.analytics_views.dashboard_queries._matching_data_names")
    @patch("apps.consumption.analytics_views.dashboard_connection")
    def test_service_unavailable_returns_503(self, dashboard_connection, matching_data_names, request_period_comparison):
        self.authenticate()
        dashboard_connection.return_value.__enter__.return_value = object()
        matching_data_names.return_value = None
        request_period_comparison.side_effect = AnalyticsServiceUnavailable("Аналитический сервис недоступен")

        response = self.client.get(
            self.endpoint,
            {
                "period1_from": "2021-01-01",
                "period1_to": "2021-01-02",
                "period2_from": "2021-01-03",
                "period2_to": "2021-01-04",
            },
        )

        self.assertEqual(response.status_code, 503)
