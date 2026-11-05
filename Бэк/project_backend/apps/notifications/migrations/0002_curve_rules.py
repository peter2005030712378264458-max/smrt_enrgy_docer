from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('notifications','0001_initial')]
    operations = [
        migrations.AddField('notificationpreference','baseline_config',models.JSONField(default=dict,db_default={})),
        migrations.AddField('notificationpreference','rule_enabled',models.BooleanField(default=False,db_default=False)),
        migrations.AddField('dailyconsumptionreport','baseline_snapshot',models.JSONField(default=dict,db_default={})),
        migrations.AddField('dailyconsumptionreport','delivery_snapshot',models.JSONField(default=dict,db_default={})),
        migrations.AddField('dailyconsumptionreport','configuration_key',models.CharField(max_length=64,default='',db_default='')),
        migrations.RemoveConstraint('dailyconsumptionreport','notifications_user_day_unique'),
        migrations.AddConstraint('dailyconsumptionreport',models.UniqueConstraint(
            fields=('user','day','configuration_key'),name='notifications_user_day_rule_unique')),
    ]
