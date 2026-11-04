"""Only for the isolated notification test PostgreSQL."""
from .settings import *  # noqa
import os

if os.environ.get('POSTGRES_HOST') != 'smart-energy-notification-test-db':
    raise RuntimeError('Use the isolated notification database container')
DATABASES['default']['OPTIONS']['options'] = '-c search_path=public'
DATABASES['analytics'] = {**DATABASES['default'],'TEST':{'MIRROR':'default'}}
DATABASES['source'] = {**DATABASES['default'],'TEST':{'MIRROR':'default'}}
