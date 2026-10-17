"""Disposable UI fixture, reachable on loopback only. Never uses project .env files."""
import os
import subprocess
import sys
import time

from .test_adapter import IntegrationTests
from adapter.service import Controller, process_job

IntegrationTests.setUpClass()
fixture = IntegrationTests()
fixture.setUp()
os.environ.update({
    'ENERGY_AGG_SOURCE_DSN': fixture.source_dsn,
    'ENERGY_AGG_TARGET_DSN': fixture.target_dsn,
    'ENERGY_ADAPTER_TOKEN': 'adapter-ui-test-only-token-123456789',
    'ENERGY_ADAPTER_URL': 'http://127.0.0.1:8002',
    'ENERGY_USE_STORED_ENERGY': '1',
    'POSTGRES_HOST': 'db', 'POSTGRES_PORT': '5432',
    'POSTGRES_USER': 'postgres', 'POSTGRES_PASSWORD': 'adapter-test-only',
    'DJANGO_POSTGRES_DB': 'test_adapter_target', 'SOURCE_POSTGRES_DB': 'test_adapter_source',
    'ENERGY_AGG_POSTGRES_DB': 'test_adapter_target',
    'DJANGO_DEBUG': '1', 'DJANGO_ALLOWED_HOSTS': 'localhost,127.0.0.1',
    'DJANGO_SECRET_KEY': 'adapter-ui-fixture-not-a-production-secret-key',
    'DJANGO_REFRESH_COOKIE_SECURE': '0', 'DJANGO_SETTINGS_MODULE': 'config.settings',
})
controller = Controller(fixture.store, {'paradigm': fixture.source_dsn})
profile = controller.dispatch('POST', '/profiles', fixture.profile)
preview = fixture.store.enqueue(profile['id'], 'preview', __import__('datetime').date(2026, 10, 15), __import__('datetime').date(2026, 10, 16))
process_job(controller, preview)
fixture.store.query("UPDATE adapter_control.jobs SET status='succeeded' WHERE id=%s", (preview['id'],))
subprocess.run([sys.executable, 'manage.py', 'migrate', '--noinput'], cwd='/app/backend', check=True)
sys.path.insert(0, '/app/backend')
import django
django.setup()
from apps.users.models import User
User.objects.filter(email='operator@example.test').delete()
User.objects.create_superuser('operator@example.test', 'AdapterTest123!', first_name='Test', last_name='Operator')
processes = [
    subprocess.Popen([sys.executable, '-m', 'adapter', 'serve']),
    subprocess.Popen([sys.executable, 'manage.py', 'runserver', '0.0.0.0:5000', '--noreload'], cwd='/app/backend'),
]
try:
    while all(p.poll() is None for p in processes):
        time.sleep(1)
finally:
    for process in processes:
        process.terminate()
