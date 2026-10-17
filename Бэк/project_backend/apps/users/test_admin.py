from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.test import Client, SimpleTestCase, override_settings


class AdminRoutingTests(SimpleTestCase):
    @override_settings(DEBUG=False, ALLOWED_HOSTS=['testserver'])
    def test_anonymous_admin_redirects_to_admin_login(self):
        response = self.client.get('/admin/')
        self.assertRedirects(response, '/admin/login/?next=/admin/', fetch_redirect_response=False)

    @override_settings(DEBUG=False, ALLOWED_HOSTS=['testserver'])
    def test_login_form_is_django_admin(self):
        response = self.client.get('/admin/login/?next=/admin/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="username"')
        self.assertContains(response, '/static/admin/css/base.css')

    def test_collected_admin_assets_work_without_debug(self):
        with TemporaryDirectory() as directory, override_settings(
            DEBUG=False, STATIC_ROOT=directory, ALLOWED_HOSTS=['testserver'],
            WHITENOISE_USE_FINDERS=False, WHITENOISE_AUTOREFRESH=False,
        ):
            call_command('collectstatic', interactive=False, verbosity=0)
            client = Client()
            for path, content_type in (
                ('/static/admin/css/base.css', 'text/css'),
                ('/static/admin/js/core.js', 'text/javascript'),
            ):
                with self.subTest(path=path):
                    response = client.get(path)
                    self.assertEqual(response.status_code, 200)
                    self.assertIn(content_type, response['Content-Type'])
                    response.close()
