from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from zoneinfo import ZoneInfo

from .energy import BASELINE_DAYS, MIN_BASELINE_DAYS
from .curve import normalize_rule
from .hourly import available_dates
from .preview import preview_curve
from .models import DailyConsumptionReport, NotificationPreference


class PreferenceInput(serializers.Serializer):
    enabled = serializers.BooleanField(required=False)
    baseline = serializers.JSONField(required=False)

    def validate_baseline(self, value):
        try:
            return normalize_rule(value)
        except ValueError as error:
            raise serializers.ValidationError(str(error)) from error

    threshold_percent = serializers.DecimalField(max_digits=7, decimal_places=2,
        min_value=Decimal('0.01'), max_value=Decimal('10000'), required=False)


def preference_data(preference):
    return {'enabled': preference.effective_enabled, 'baseline': normalize_rule(preference.baseline_config),
            'legacy_rule': not bool(preference.baseline_config), 'threshold_percent': str(preference.threshold_percent),
            'email': preference.user.email, 'baseline_days': BASELINE_DAYS,
            'minimum_baseline_days': MIN_BASELINE_DAYS,
            'mail_configured': bool(settings.EMAIL_HOST and settings.DEFAULT_FROM_EMAIL),
            'last_check_at': preference.last_check_at}


class PreferenceView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        preference, _ = NotificationPreference.objects.get_or_create(user=request.user)
        return Response(preference_data(preference))

    def patch(self, request):
        data = PreferenceInput(data=request.data)
        data.is_valid(raise_exception=True)
        if set(request.data) - {'enabled', 'threshold_percent', 'baseline'}:
            return Response({'detail': 'Допустимы только enabled, threshold_percent и baseline.'}, status=400)
        with transaction.atomic():
            NotificationPreference.objects.get_or_create(user=request.user)
            preference = NotificationPreference.objects.select_for_update().get(user=request.user)
            was_enabled = preference.effective_enabled
            if data.validated_data.get('enabled') and not was_enabled:
                preference.monitor_from = timezone.now().astimezone(ZoneInfo('Europe/Moscow')).date() - timedelta(days=1)
                preference.last_processed = None
            values = dict(data.validated_data)
            baseline = values.pop('baseline', None)
            desired_enabled = values.pop('enabled', was_enabled)
            if baseline is not None or preference.baseline_config:
                if baseline is not None:
                    if baseline != preference.baseline_config:
                        preference.last_processed = None
                        preference.monitor_from = timezone.now().astimezone(ZoneInfo('Europe/Moscow')).date()-timedelta(days=1)
                    preference.baseline_config = baseline
                # Old workers only understand daily constants. Disable their path for this rule.
                preference.enabled = False
                preference.rule_enabled = desired_enabled
            else:
                preference.enabled = desired_enabled
            for key, value in values.items():
                setattr(preference, key, value)
            preference.save()
        return Response(preference_data(preference))


class ReportsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            page = int(request.query_params.get('page', '1'))
            if not 1 <= page <= 1000000:
                raise ValueError
        except ValueError:
            return Response({'detail': 'Некорректный номер страницы.'}, status=400)
        queryset = DailyConsumptionReport.objects.filter(user=request.user)
        count = queryset.count()
        fields = ('id', 'day', 'status', 'energy_kwh', 'average_kwh', 'deviation_percent',
                  'threshold_percent', 'baseline_days', 'baseline_dates', 'meter_count',
                  'explanation', 'configuration_key', 'baseline_snapshot', 'delivery_snapshot', 'email_status', 'email_to', 'email_attempts', 'email_error',
                  'email_sent_at', 'created_at', 'updated_at')
        return Response({'count': count, 'page': page, 'page_size': 20,
                         'results': list(queryset.values(*fields)[(page-1)*20:page*20])})


class CurveInput(PreferenceInput):
    day = serializers.DateField(required=True)


class CurvePreviewView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(available_dates())

    def post(self, request):
        if set(request.data) - {'day','baseline','threshold_percent'}:
            return Response({'detail':'Неизвестные параметры проверки'},status=400)
        data = CurveInput(data=request.data)
        data.is_valid(raise_exception=True)
        preference = NotificationPreference.objects.filter(user=request.user).first()
        rule = data.validated_data.get('baseline') or normalize_rule(preference.baseline_config if preference else {})
        threshold = data.validated_data.get('threshold_percent', preference.threshold_percent if preference else Decimal(30))
        try:
            result = preview_curve(data.validated_data['day'],rule,threshold)
        except ValueError as error:
            return Response({'detail':str(error)},status=400)
        return Response(result)
