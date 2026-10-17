import os
import subprocess
import sys

commands = [
    ([sys.executable, '-m', 'unittest', 'discover', '-s', 'adapter/tests', '-v'], '/app'),
    ([sys.executable, 'manage.py', 'test', 'apps.consumption', 'apps.aggregation', 'apps.users', '--verbosity', '1'], '/app/backend'),
    ([sys.executable, '-m', 'pytest', '-q'], '/app/analytics'),
    ([sys.executable, '-m', 'adapter.tests.test_energy_consumers'], '/app'),
]
for command, cwd in commands:
    result = subprocess.run(command, cwd=cwd, env={**os.environ, 'DJANGO_DEBUG': '1', 'DJANGO_ALLOWED_HOSTS': 'testserver,localhost'})
    if result.returncode:
        raise SystemExit(result.returncode)
