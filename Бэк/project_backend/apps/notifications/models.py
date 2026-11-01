from django.conf import settings
from django.db import models


class NotificationPreference(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    enabled = models.BooleanField(default=False)
    threshold_percent = models.DecimalField(max_digits=7, decimal_places=2, default=30)
    monitor_from = models.DateField(null=True, blank=True)
    last_processed = models.DateField(null=True, blank=True)
    last_check_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class DailyConsumptionReport(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    day = models.DateField()
    status = models.CharField(max_length=20, default='insufficient')
    energy_kwh = models.DecimalField(max_digits=30, decimal_places=6, null=True)
    average_kwh = models.DecimalField(max_digits=30, decimal_places=6, null=True)
    deviation_percent = models.DecimalField(max_digits=50, decimal_places=6, null=True)
    threshold_percent = models.DecimalField(max_digits=7, decimal_places=2)
    baseline_days = models.PositiveSmallIntegerField(default=0)
    meter_count = models.PositiveIntegerField(default=0)
    baseline_dates = models.JSONField(default=list)
    explanation = models.TextField(blank=True)
    email_status = models.CharField(max_length=20, default='not_required')
    email_to = models.EmailField(blank=True)
    email_attempts = models.PositiveSmallIntegerField(default=0)
    email_error = models.CharField(max_length=200, blank=True)
    email_sent_at = models.DateTimeField(null=True)
    next_email_attempt = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-day', '-id']
        constraints = [models.UniqueConstraint(fields=['user', 'day'], name='notifications_user_day_unique')]
