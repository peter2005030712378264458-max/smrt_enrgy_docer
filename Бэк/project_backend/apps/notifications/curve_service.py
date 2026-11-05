"""Hourly rule scheduling and report snapshots. Preview never calls this module."""
from datetime import timedelta
from decimal import Decimal
import json
import logging

from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone

from .curve import normalize_rule, rule_key
from .models import DailyConsumptionReport, NotificationPreference
from .preview import preview_curve

LOG = logging.getLogger(__name__)


def frozen(value):
    return json.loads(json.dumps(value,cls=DjangoJSONEncoder))


def run_curve_cycle(now):
    from .service import deliver_report
    from zoneinfo import ZoneInfo
    today = now.astimezone(ZoneInfo('Europe/Moscow')).date()
    preferences = NotificationPreference.objects.filter(rule_enabled=True,user__is_active=True).select_related('user')
    cache = {}
    for snapshot in preferences:
        rule = normalize_rule(snapshot.baseline_config)
        signature = rule_key(rule,snapshot.threshold_percent)
        if rule['mode']=='live':
            days = [today]
        else:
            begin = max(snapshot.monitor_from or today-timedelta(days=1),
                        snapshot.last_processed+timedelta(days=1) if snapshot.last_processed else snapshot.monitor_from or today-timedelta(days=1))
            days = [begin+timedelta(days=i) for i in range(min(max((today-begin).days,0),31))]
            days.extend(DailyConsumptionReport.objects.filter(user=snapshot.user,configuration_key=signature,
                status='insufficient',day__gte=today-timedelta(days=7),day__lt=today).values_list('day',flat=True))
        for day in sorted(set(days)):
            cache_key = (day,signature)
            try:
                if cache_key not in cache:
                    cache[cache_key] = preview_curve(day,rule,snapshot.threshold_percent,now)
                result = cache[cache_key]
            except Exception as error:
                # Keep one unavailable source from preventing all other reports/deliveries.
                LOG.warning('Curve data unavailable: %s',type(error).__name__)
                continue
            with transaction.atomic():
                preference = NotificationPreference.objects.select_for_update().get(pk=snapshot.pk)
                if not preference.effective_enabled or rule_key(preference.baseline_config,preference.threshold_percent)!=signature:
                    break
                report = DailyConsumptionReport.objects.select_for_update().filter(
                    user=preference.user,day=day,configuration_key=signature).first()
                if report and rule['mode']=='daily' and report.status!='insufficient':
                    continue
                metrics = {key:result[key] for key in ('status','energy_kwh','average_kwh','deviation_percent',
                    'baseline_days','baseline_dates','meter_count','explanation')}
                if report is None:
                    report = DailyConsumptionReport(user=preference.user,day=day,configuration_key=signature,
                        threshold_percent=preference.threshold_percent)
                for key,value in metrics.items():
                    setattr(report,key,value)
                report.baseline_snapshot = frozen(result)
                if result['status']=='critical' and report.email_status=='not_required':
                    report.email_status='pending'
                report.save()
                preference.last_check_at=now
                if rule['mode']=='daily':
                    preference.last_processed=max(preference.last_processed or day,day)
                preference.save()
        NotificationPreference.objects.filter(pk=snapshot.pk,rule_enabled=True).update(last_check_at=now)
        for report_id in DailyConsumptionReport.objects.filter(user=snapshot.user,configuration_key=signature,
                status='critical',email_status__in=['pending','failed']).values_list('pk',flat=True):
            deliver_report(report_id)
