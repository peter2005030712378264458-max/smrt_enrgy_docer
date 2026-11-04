from pathlib import Path
import signal
import threading

from django.core.management.base import BaseCommand
from django.db import connection, connections
from django.utils import timezone

from apps.notifications.service import _run_legacy_cycle
from apps.notifications.curve_service import run_curve_cycle


def run_locked(callback, key):
    acquired = False
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_try_advisory_lock(%s)', [key])
            acquired = cursor.fetchone()[0]
        if acquired:
            callback()
        return acquired
    finally:
        if acquired and connection.connection is not None:
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_advisory_unlock(%s)', [key])
            except Exception:
                connection.close()
        connections.close_all()


class Command(BaseCommand):
    help = 'Evaluate legacy daily reports and configurable hourly curves.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')
        parser.add_argument('--interval', type=int, default=60)

    def handle(self, *args, **options):
        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        while not stop.is_set():
            # Legacy VM workers must not starve the new curve rules.
            for callback,key in ((_run_legacy_cycle,482019773),
                                 (lambda:run_curve_cycle(timezone.now()),482019774)):
                try:
                    run_locked(callback,key)
                except Exception as error:
                    self.stderr.write('Notification cycle failed: '+type(error).__name__)
                    if options['once']:
                        raise
            Path('/tmp/notifications-heartbeat').touch()
            if options['once']:
                return
            stop.wait(max(5,options['interval']))
