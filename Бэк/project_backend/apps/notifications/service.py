from datetime import timedelta
from decimal import Decimal
from email.utils import parseaddr
import logging
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.mail import EmailMessage, get_connection
from django.db import transaction
from django.utils import timezone

from .energy import BASELINE_DAYS, evaluate, load_days
from .models import DailyConsumptionReport, NotificationPreference

LOG = logging.getLogger(__name__)


def message_text(report):
    deviation = (f'{report.deviation_percent:+.2f}%' if report.deviation_percent is not None
                 else 'процент не определён при нулевом среднем')
    return (f'Smart Energy: критическое отклонение суточного потребления\n\n'
            f'Сутки по Москве: {report.day:%d.%m.%Y}\n'
            f'Суммарная энергия: {report.energy_kwh:.3f} кВт·ч\n'
            f'Среднее за предыдущие 30 дней: {report.average_kwh:.3f} кВт·ч\n'
            f'Полных сопоставимых суток в среднем: {report.baseline_days}\n'
            f'Счётчиков: {report.meter_count}\n'
            f'Отклонение: {deviation}\nПорог: {report.threshold_percent}%\n'
            f'{report.explanation}\n\n'
            'Отчёт сохранён на странице «Уведомления» приложения. '
            'Отключить письма или изменить порог можно на этой странице.\n')


def deliver_report(report_id):
    # Serialize opt-out with sending. No new message can start after a saved
    # opt-out. The preference lock is held for at most the SMTP timeout.
    owner = DailyConsumptionReport.objects.only('user_id').get(pk=report_id).user_id
    with transaction.atomic():
        preference = NotificationPreference.objects.select_for_update().select_related('user').get(user_id=owner)
        report = DailyConsumptionReport.objects.select_for_update().get(pk=report_id)
        if not preference.enabled or not preference.user.is_active or report.status != 'critical':
            return
        if report.email_status not in ('pending', 'failed') or report.email_attempts >= 5:
            return
        if report.next_email_attempt and report.next_email_attempt > timezone.now():
            return
        if not settings.EMAIL_HOST or not settings.DEFAULT_FROM_EMAIL:
            report.email_error = 'SMTP не настроен. Отчёт доступен в приложении.'
            report.save(update_fields=['email_error', 'updated_at'])
            return
        report.email_to = preference.user.email
        connection = get_connection()
        try:
            connection.open()
        except Exception:
            report.email_status = 'failed'
            report.email_attempts += 1
            report.email_error = ('Не удалось подключиться к SMTP. Будет повторная попытка.'
                if report.email_attempts < 5 else 'SMTP недоступен после 5 попыток. Требуется проверка почтовых настроек.')
            report.next_email_attempt = timezone.now() + timedelta(minutes=15)
            report.save()
            return
        # Persist the in-flight state before SMTP submission. A crash during
        # submission must not blindly resend a possibly accepted message.
        report.email_status = 'sending'
        report.email_attempts += 1
        report.email_error = ''
        report.save()
    try:
        domain = parseaddr(settings.DEFAULT_FROM_EMAIL)[1].rpartition('@')[2] or 'notifications.local'
        message_id = f'<smart-energy-report-{report.pk}-{report.created_at:%Y%m%d%H%M%S%f}@{domain}>'
        message = EmailMessage(f'Smart Energy: отклонение потребления за {report.day:%d.%m.%Y}',
            message_text(report), settings.DEFAULT_FROM_EMAIL, [report.email_to], connection=connection,
            headers={'Message-ID': message_id})
        # Recheck enabled immediately before sending; hold lock through SMTP.
        with transaction.atomic():
            preference = NotificationPreference.objects.select_for_update().select_related('user').get(user_id=owner)
            if not preference.enabled or not preference.user.is_active:
                DailyConsumptionReport.objects.filter(pk=report_id).update(email_status='pending')
                return
            message.to = [preference.user.email]
            DailyConsumptionReport.objects.filter(pk=report_id).update(email_to=preference.user.email)
            accepted = message.send(fail_silently=False)
            if accepted != 1:
                raise RuntimeError('SMTP did not accept a message')
            DailyConsumptionReport.objects.filter(pk=report_id).update(
                email_status='sent', email_sent_at=timezone.now(), email_error='', next_email_attempt=None)
    except Exception:
        DailyConsumptionReport.objects.filter(pk=report_id).update(email_status='unknown',
            email_error='Отправка прервана; доставка неизвестна. Автоматический повтор исключён во избежание дубликата.')
    finally:
        try:
            connection.close()
        except Exception:
            LOG.warning('SMTP connection close failed')


def run_cycle(now=None):
    now = now or timezone.now()
    today = now.astimezone(ZoneInfo('Europe/Moscow')).date()
    # In-flight messages left by an interrupted worker are deliberately not
    # retried. SMTP cannot guarantee exactly-once delivery after a disconnect.
    DailyConsumptionReport.objects.filter(email_status='sending', updated_at__lt=now-timedelta(minutes=5)).update(
        email_status='unknown', email_error='Процесс отправки был прерван; статус доставки неизвестен.')
    preferences = NotificationPreference.objects.filter(enabled=True, user__is_active=True).select_related('user')
    cache = {}
    for snapshot in preferences:
        first = max(snapshot.monitor_from or today-timedelta(days=1),
                    (snapshot.last_processed + timedelta(days=1)) if snapshot.last_processed else snapshot.monitor_from or today-timedelta(days=1))
        days = []
        while first < today and len(days) < 31:
            days.append(first)
            first += timedelta(days=1)
        days.extend(DailyConsumptionReport.objects.filter(user=snapshot.user, status='insufficient',
            day__gte=max(snapshot.monitor_from or today, today-timedelta(days=7)), day__lt=today).values_list('day', flat=True))
        for day in sorted(set(days)):
            if day not in cache:
                cache[day] = evaluate(day, load_days(day-timedelta(days=BASELINE_DAYS), day+timedelta(days=1)), Decimal(1))
            facts = dict(cache[day])
            with transaction.atomic():
                preference = NotificationPreference.objects.select_for_update().get(pk=snapshot.pk)
                if not preference.enabled:
                    break
                report = DailyConsumptionReport.objects.filter(user_id=preference.user_id, day=day).first()
                if report is None or report.status == 'insufficient':
                    if facts['status'] != 'insufficient':
                        deviation = facts['deviation_percent']
                        facts['status'] = 'critical' if ((deviation is None and facts['energy_kwh'] != 0)
                            or (deviation is not None and abs(deviation) >= preference.threshold_percent)) else 'normal'
                    defaults = {**facts, 'threshold_percent': preference.threshold_percent,
                                'email_status': 'pending' if facts['status']=='critical' else 'not_required'}
                    DailyConsumptionReport.objects.update_or_create(user_id=preference.user_id, day=day, defaults=defaults)
                preference.last_processed = max(preference.last_processed or day, day)
                preference.last_check_at = now
                preference.save()
        NotificationPreference.objects.filter(pk=snapshot.pk, enabled=True).update(last_check_at=now)
    for report_id in DailyConsumptionReport.objects.filter(user__notificationpreference__enabled=True,
            user__is_active=True, status='critical', email_status__in=['pending', 'failed']).values_list('pk', flat=True):
        deliver_report(report_id)
