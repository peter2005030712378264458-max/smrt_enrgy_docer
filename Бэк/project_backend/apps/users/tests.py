from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase, override_settings
from rest_framework.response import Response
from rest_framework.test import APIClient
from rest_framework_simplejwt.views import TokenObtainPairView

from config.runtime_secret import ensure_secret_key


class RuntimeSecretTests(SimpleTestCase):
    def test_generated_key_is_preserved_across_startups(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "secrets" / "django_secret_key"
            environ = {"DJANGO_SECRET_KEY_FILE": str(path)}

            ensure_secret_key(environ)
            first_key = path.read_text(encoding="utf-8").strip()
            self.assertGreaterEqual(len(first_key), 64)
            ensure_secret_key(environ)
            self.assertEqual(path.read_text(encoding="utf-8").strip(), first_key)

    def test_existing_file_key_is_preserved(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "django_secret_key"
            path.write_text("existing-signing-key\n", encoding="utf-8")

            ensure_secret_key({"DJANGO_SECRET_KEY_FILE": str(path)})

            self.assertEqual(path.read_text(encoding="utf-8"), "existing-signing-key\n")

    def test_configured_key_does_not_require_a_file(self):
        ensure_secret_key({"DJANGO_SECRET_KEY": "configured-signing-key"})

    def test_empty_persistent_file_fails_instead_of_changing_sessions(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "django_secret_key"
            path.touch()

            with self.assertRaisesMessage(ValueError, "Signing key file is empty"):
                ensure_secret_key({"DJANGO_SECRET_KEY_FILE": str(path)})


class CookieSessionTests(SimpleTestCase):
    def setUp(self):
        self.client = APIClient()

    def login(self):
        with patch.object(
            TokenObtainPairView,
            "post",
            return_value=Response({"access": "access-token", "refresh": "refresh-token"}),
        ):
            return self.client.post(
                "/api/auth/login/",
                {"email": "test@example.com", "password": "example"},
                format="json",
            )

    @override_settings(AUTH_REFRESH_COOKIE_SECURE=True)
    def test_https_login_preserves_refresh_session_across_browser_restarts(self):
        response = self.login()
        cookie = response.cookies["refresh_token"]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"access": "access-token"})
        self.assertTrue(cookie["secure"])
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Lax")
        self.assertEqual(cookie["max-age"], int(settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"].total_seconds()))
        self.assertTrue(cookie["expires"])

    @override_settings(AUTH_REFRESH_COOKIE_SECURE=False)
    def test_local_http_login_keeps_refresh_cookie_usable(self):
        self.assertFalse(self.login().cookies["refresh_token"]["secure"])

    def test_refresh_without_session_returns_401(self):
        response = self.client.post("/api/auth/refresh/", {}, format="json")

        self.assertEqual(response.status_code, 401)
