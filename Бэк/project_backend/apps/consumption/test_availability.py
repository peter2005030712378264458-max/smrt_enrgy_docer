from unittest.mock import patch
from django.test import SimpleTestCase
from rest_framework.test import APIClient
from .tests import DummyUser


class AvailabilityViewTests(SimpleTestCase):
    url='/api/consumption/dashboard/device-availability/'

    def setUp(self):
        self.client=APIClient()

    def test_requires_login(self):
        self.assertEqual(self.client.get(self.url).status_code,401)

    @patch('apps.consumption.availability_views.request_adapter')
    def test_normal_user_can_read_only_the_report(self,reader):
        user=DummyUser()
        user.is_staff=False
        self.client.force_authenticate(user=user)
        reader.return_value=({'state':'pending'},200)
        self.assertEqual(self.client.get(self.url,{'period':'month'}).status_code,200)
        reader.assert_called_once_with('GET','device-availability?period=month&refresh=0')
        reader.reset_mock()
        for parameters in ({'period':'all'},{'connection':'arbitrary'},{'refresh':'yes'}):
            self.assertEqual(self.client.get(self.url,parameters).status_code,400)
        reader.assert_not_called()
        self.assertEqual(self.client.post(self.url,{}).status_code,405)
        self.assertEqual(self.client.get('/api/aggregation/jobs/').status_code,403)
