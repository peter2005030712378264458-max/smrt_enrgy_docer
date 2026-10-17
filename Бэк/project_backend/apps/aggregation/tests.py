from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIClient

from apps.consumption import dashboard_queries


class AggregationAccessTests(SimpleTestCase):
    def setUp(self):
        self.client = APIClient()

    def user(self, staff=False):
        self.client.force_authenticate(user=SimpleNamespace(is_authenticated=True, is_active=True, is_staff=staff))

    def test_anonymous_and_ordinary_user_cannot_manage(self):
        self.assertEqual(self.client.get('/api/aggregation/jobs/').status_code, 401)
        self.user()
        self.assertEqual(self.client.get('/api/aggregation/jobs/').status_code, 403)
        self.assertFalse(self.client.get('/api/aggregation/capabilities/').data['can_manage'])

    @patch('apps.aggregation.views.request_adapter')
    def test_staff_can_enqueue_and_read_without_database_credentials(self, adapter):
        self.user(True)
        adapter.return_value = ({'id': 'job', 'status': 'queued'}, 200)
        response = self.client.post('/api/aggregation/jobs/', {'kind': 'preview', 'profile_id': 'p'}, format='json')
        self.assertEqual(response.status_code, 200)
        adapter.assert_called_once_with('POST', 'jobs', {'kind': 'preview', 'profile_id': 'p'})

    @patch('apps.aggregation.views.request_adapter')
    def test_proxy_restricts_paths_and_preserves_encoded_alias(self, adapter):
        self.user(True)
        self.assertEqual(self.client.get('/api/aggregation/not-allowed/').status_code, 404)
        adapter.assert_not_called()
        adapter.return_value = ([], 200)
        self.client.get('/api/aggregation/tables/', {'connection': 'customer & next'})
        self.assertEqual(adapter.call_args.args[1], 'tables?connection=customer+%26+next')

    @override_settings(ENERGY_ADAPTER_URL='', ENERGY_ADAPTER_TOKEN='')
    def test_missing_service_has_clear_503(self):
        self.user(True)
        self.assertEqual(self.client.get('/api/aggregation/jobs/').status_code, 503)


@override_settings(ENERGY_USE_STORED_ENERGY=True)
class StoredEnergyTests(SimpleTestCase):
    def test_saved_energy_is_used_for_every_granularity(self):
        for granularity in ('hour', 'day', 'week'):
            expression = dashboard_queries._bucket_energy_sql(granularity)
            self.assertIn('SUM(a.energy_kwh)', expression)
            self.assertNotIn('active_power', expression)
            self.assertIn('COUNT(a.energy_kwh)', expression)

    def test_missing_room_energy_does_not_become_zero(self):
        result = dashboard_queries._aggregate_room_load_rows([
            {'energy_kwh': 1, 'points_count': 1}, {'energy_kwh': None, 'points_count': 1},
        ], 'Other')
        self.assertIsNone(result['energy_kwh'])

    @override_settings(ENERGY_USE_STORED_ENERGY=False)
    def test_rollback_switch_keeps_legacy_energy(self):
        self.assertIn('active_power_w_avg', dashboard_queries._bucket_energy_sql('hour'))
