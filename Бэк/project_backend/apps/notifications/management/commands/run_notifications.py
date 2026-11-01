from pathlib import Path
import signal
import threading

from django.core.management.base import BaseCommand
from django.db import connection, connections

from apps.notifications.service import run_cycle


class Command(BaseCommand):
    help = 'Evaluate daily energy reports and deliver opted-in email notifications.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')
        parser.add_argument('--interval', type=int, default=60)

    def handle(self, *args, **options):
        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        while not stop.is_set():
            acquired = False
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_try_advisory_lock(482019773)')
                    acquired = cursor.fetchone()[0]
                if acquired:
                    run_cycle()
                Path('/tmp/notifications-heartbeat').touch()
            except Exception as error:
                # Never log credentials, recipient addresses, or SMTP replies.
                self.stderr.write('Notification cycle failed: '+type(error).__name__)
                if options['once']:
                    raise
            finally:
                if acquired and connection.connection is not None:
                    try:
                        with connection.cursor() as cursor:
                            cursor.execute('SELECT pg_advisory_unlock(482019773)')
                    except Exception:
                        connection.close()
                connections.close_all()
            if options['once']:
                return
            stop.wait(max(5, options['interval']))
