from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch
from contextlib import contextmanager
from email.parser import BytesParser
import socketserver
import threading

from django.core import mail
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from apps.users.models import User
from .energy import evaluate, load_days
from .models import DailyConsumptionReport, NotificationPreference
from .service import deliver_report, run_cycle

DAY = date(2026, 10, 30)
NOW = datetime(2026, 10, 31, 0, 10, tzinfo=dt_timezone.utc)


def readings(actual='130'):
    result = {DAY-timedelta(days=i): {'A': Decimal(100), 'B': Decimal(0)} for i in range(1, 31)}
    result[DAY] = {'A': Decimal(actual), 'B': Decimal(0)}
    return result


@contextmanager
def local_smtp():
    received = []
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.wfile.write(b'220 test-smtp\r\n')
            while True:
                line = self.rfile.readline()
                if not line:
                    return
                command = line.split(b' ', 1)[0].strip().upper()
                if command == b'DATA':
                    self.wfile.write(b'354 send message\r\n')
                    body = []
                    while True:
                        part = self.rfile.readline()
                        if part in (b'.\r\n', b''):
                            break
                        body.append(part)
                    received.append(BytesParser().parsebytes(b''.join(body)))
                    self.wfile.write(b'250 accepted\r\n')
                elif command == b'QUIT':
                    self.wfile.write(b'221 bye\r\n')
                    return
                else:
                    self.wfile.write(b'250 test-smtp\r\n')
    with socketserver.TCPServer(('127.0.0.1', 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_address[1], received
        finally:
            server.shutdown()
            thread.join()


class EvaluationTests(SimpleTestCase):
    def test_both_directions_and_exact_threshold(self):
        for actual, status in [('130', 'critical'), ('70', 'critical'), ('129.99', 'normal'), ('100', 'normal')]:
            result = evaluate(DAY, readings(actual), Decimal(30))
            self.assertEqual(result['status'], status)
            self.assertEqual(result['baseline_days'], 30)

    def test_current_day_is_not_part_of_average(self):
        self.assertEqual(evaluate(DAY, readings('1000'), 30)['average_kwh'], Decimal(100))

    def test_partial_current_energy_is_not_zero_or_partial_total(self):
        days = readings()
        days[DAY]['B'] = None
        result = evaluate(DAY, days, 30)
        self.assertEqual(result['status'], 'insufficient')
        self.assertIsNone(result['energy_kwh'])

    def test_missing_days_and_changed_meter_sets_are_excluded(self):
        days = readings()
        for offset in range(1, 25):
            days[DAY-timedelta(days=offset)].pop('B')
        result = evaluate(DAY, days, 30)
        self.assertEqual(result['baseline_days'], 6)
        self.assertEqual(result['status'], 'insufficient')
        days[DAY-timedelta(days=1)]['B'] = Decimal(0)
        self.assertEqual(evaluate(DAY, days, 30)['status'], 'critical')

    def test_zero_baseline_is_explicit_and_real_zero_is_normal(self):
        days = {DAY-timedelta(days=i): {'A': Decimal(0)} for i in range(31)}
        self.assertEqual(evaluate(DAY, days, 30)['status'], 'normal')
        days[DAY]['A'] = Decimal(1)
        result = evaluate(DAY, days, 30)
        self.assertEqual(result['status'], 'critical')
        self.assertIsNone(result['deviation_percent'])

    def test_reader_rejects_unknown_counters_nonfinite_energy_and_duplicates(self):
        cursor = MagicMock()
        cursor.fetchall.return_value = [(DAY,'A',Decimal(2),10,10), (DAY,'B',Decimal(0),10,None),
            (DAY,'C',Decimal('NaN'),10,10), (DAY,'D',Decimal(3),10,10), (DAY,'D',Decimal(3),10,10)]
        with patch('apps.notifications.energy.connections') as connections:
            connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = cursor
            result = load_days(DAY, DAY+timedelta(days=1))
        self.assertEqual(result[DAY], {'A': Decimal(2), 'B': None, 'C': None, 'D': None})


class NotificationAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('first@example.invalid', 'test-password')
        self.other = User.objects.create_user('second@example.invalid', 'test-password')
        self.client = APIClient()

    def test_both_endpoints_require_authentication(self):
        for path in ('settings/', 'reports/'):
            self.assertEqual(self.client.get('/api/notifications/'+path).status_code, 401)

    def test_defaults_are_opted_out_and_settings_are_personal(self):
        self.client.force_authenticate(self.user)
        response = self.client.get('/api/notifications/settings/')
        self.assertFalse(response.data['enabled'])
        self.assertEqual(response.data['email'], self.user.email)
        response = self.client.patch('/api/notifications/settings/', {'enabled': True, 'threshold_percent':'25.25'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(NotificationPreference.objects.get(user=self.user).threshold_percent, Decimal('25.25'))
        self.assertFalse(NotificationPreference.objects.filter(user=self.other).exists())

    def test_rejects_invalid_threshold_and_foreign_fields(self):
        self.client.force_authenticate(self.user)
        for value in ('0','-1','NaN','Infinity','10001','1.234'):
            self.assertEqual(self.client.patch('/api/notifications/settings/', {'threshold_percent':value}, format='json').status_code, 400)
        self.assertEqual(self.client.patch('/api/notifications/settings/', {'user_id':self.other.pk}, format='json').status_code, 400)

    def test_report_isolation_and_pagination(self):
        own = DailyConsumptionReport.objects.create(user=self.user, day=DAY, threshold_percent=30)
        DailyConsumptionReport.objects.create(user=self.other, day=DAY, threshold_percent=30)
        self.client.force_authenticate(self.user)
        response = self.client.get('/api/notifications/reports/')
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['id'], own.pk)
        self.assertEqual(self.client.get('/api/notifications/reports/?page=0').status_code, 400)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', EMAIL_HOST='test-only', DEFAULT_FROM_EMAIL='alerts@example.invalid')
class WorkerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('recipient@example.invalid', 'test-password')
        self.preference = NotificationPreference.objects.create(user=self.user, enabled=True, monitor_from=DAY)
        mail.outbox = []

    def cycle(self, actual='130'):
        with patch('apps.notifications.service.load_days', return_value=readings(actual)):
            run_cycle(NOW)

    def test_critical_report_and_email_once_across_repeated_cycles(self):
        self.cycle()
        self.cycle()
        report = DailyConsumptionReport.objects.get(user=self.user)
        self.assertEqual(report.status, 'critical')
        self.assertEqual(report.email_status, 'sent')
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.user.email])
        self.assertIn('30.00%', mail.outbox[0].body)
        self.assertIn('130.000', mail.outbox[0].body)

    def test_normal_day_is_reported_without_email(self):
        self.cycle('100')
        self.assertEqual(DailyConsumptionReport.objects.get().status, 'normal')
        self.assertEqual(len(mail.outbox), 0)

    def test_opt_out_prevents_reports_and_mail(self):
        self.preference.enabled = False
        self.preference.save()
        self.cycle()
        self.assertFalse(DailyConsumptionReport.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_insufficient_report_can_be_updated_when_aggregates_arrive(self):
        with patch('apps.notifications.service.load_days', return_value={}):
            run_cycle(NOW)
        self.assertEqual(DailyConsumptionReport.objects.get().status, 'insufficient')
        self.assertEqual(len(mail.outbox), 0)
        self.cycle()
        self.assertEqual(DailyConsumptionReport.objects.count(), 1)
        self.assertEqual(DailyConsumptionReport.objects.get().status, 'critical')
        self.assertEqual(len(mail.outbox), 1)

    def test_threshold_changes_preserve_existing_report(self):
        self.cycle('100')
        self.preference.threshold_percent = Decimal('0.01')
        self.preference.save()
        self.cycle('200')
        self.assertEqual(DailyConsumptionReport.objects.get().energy_kwh, Decimal(100))
        self.assertEqual(DailyConsumptionReport.objects.get().threshold_percent, Decimal(30))
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(EMAIL_HOST='')
    def test_missing_smtp_is_visible_without_discarding_report(self):
        self.cycle()
        report = DailyConsumptionReport.objects.get()
        self.assertEqual(report.email_status, 'pending')
        self.assertIn('SMTP', report.email_error)
        self.assertEqual(len(mail.outbox), 0)

    def test_connection_failure_can_retry_and_unknown_submission_is_not_retried(self):
        self.cycle('100')
        report = DailyConsumptionReport.objects.get()
        report.status='critical'; report.email_status='pending'; report.save()
        connection = MagicMock()
        connection.open.side_effect = OSError('private SMTP reply must not be persisted')
        with patch('apps.notifications.service.get_connection', return_value=connection):
            deliver_report(report.pk)
        report.refresh_from_db()
        self.assertEqual(report.email_status, 'failed')
        self.assertNotIn('private', report.email_error)
        report.next_email_attempt=None; report.save()
        connection.open.side_effect=None
        with patch('apps.notifications.service.get_connection', return_value=connection), patch('apps.notifications.service.EmailMessage') as message:
            message.return_value.send.side_effect=OSError('ambiguous delivery')
            deliver_report(report.pk)
        report.refresh_from_db()
        self.assertEqual(report.email_status, 'unknown')
        deliver_report(report.pk)
        self.assertEqual(len(mail.outbox), 0)

    def test_future_moscow_day_is_not_evaluated_and_initial_activation_has_no_historical_spam(self):
        self.cycle()
        self.assertEqual(list(DailyConsumptionReport.objects.values_list('day', flat=True)), [DAY])

    def test_real_smtp_transport_accepts_gmail_and_mail_ru_recipients(self):
        self.user.email = 'notification-test@gmail.com'
        self.user.save()
        second = User.objects.create_user('notification-test@mail.ru', 'test-password')
        NotificationPreference.objects.create(user=second, enabled=True, monitor_from=DAY)
        with local_smtp() as (port, received), override_settings(
            EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend', EMAIL_HOST='127.0.0.1',
            EMAIL_PORT=port, EMAIL_USE_TLS=False, EMAIL_USE_SSL=False, EMAIL_HOST_USER='', EMAIL_HOST_PASSWORD=''):
            self.cycle()
            self.cycle()
        self.assertEqual({item['To'] for item in received}, {self.user.email, second.email})
        self.assertEqual(len(received), 2)
        self.assertEqual(DailyConsumptionReport.objects.filter(email_status='sent').count(), 2)
