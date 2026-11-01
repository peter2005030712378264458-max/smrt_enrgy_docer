import os
import subprocess
import sys

commands = [
    ([sys.executable, '-m', 'unittest', 'discover', '-s', 'adapter/tests', '-v'], '/app'),
    ([sys.executable, 'manage.py', 'test', 'apps.consumption', 'apps.aggregation', 'apps.users', '--verbosity', '1'], '/app/backend'),
    ([sys.executable, 'manage.py', 'test', 'apps.notifications', '--verbosity', '1'], '/app/backend'),
    ([sys.executable, '-m', 'pytest', '-q'], '/app/analytics'),
    ([sys.executable, '-m', 'adapter.tests.test_energy_consumers'], '/app'),
]
for command, cwd in commands:
    environment = {**os.environ, 'DJANGO_DEBUG': '1', 'DJANGO_ALLOWED_HOSTS': 'testserver,localhost'}
    if 'apps.notifications' in command:
        environment.update(POSTGRES_HOST='db', POSTGRES_PORT='5432', POSTGRES_USER='postgres',
                           POSTGRES_PASSWORD='adapter-test-only', DJANGO_POSTGRES_DB='postgres')
    result = subprocess.run(command, cwd=cwd, env=environment)
    if result.returncode:
        raise SystemExit(result.returncode)
