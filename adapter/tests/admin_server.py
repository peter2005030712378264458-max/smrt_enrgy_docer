"""Django admin fixture: no working database access or migrations."""
import os
import sys

os.environ.update({
    'DJANGO_SETTINGS_MODULE': 'config.settings',
    'DJANGO_DEBUG': '0',
    'DJANGO_SECRET_KEY': 'admin-routing-test-only-signing-key-not-production',
    'DJANGO_ALLOWED_HOSTS': 'localhost,backend',
    'DJANGO_TRUST_PROXY_HEADERS': '1',
    'POSTGRES_HOST': 'no-database-for-admin-routing-test.invalid',
})
sys.path.insert(0, '/app/backend')
import django
django.setup()
from django.core.management import call_command
call_command('collectstatic', interactive=False, verbosity=0)
os.execvp('gunicorn', ['gunicorn', '--chdir', '/app/backend', 'config.wsgi:application', '--bind', '0.0.0.0:5000'])
