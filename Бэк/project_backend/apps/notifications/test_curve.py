from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import random
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.core import mail
from rest_framework.test import APIClient

from apps.users.models import User
from .curve import DEFAULT_RULE, complete_total, evaluate_curve, history_bounds, normalize_rule, rule_key, same_group
from .models import NotificationPreference, DailyConsumptionReport
from .preview import preview_curve
from .service import deliver_report, message_text, run_cycle
from .hourly import load_hours

DAY = date(2026,11,3)
NOW = datetime(2026,11,4,9,tzinfo=timezone.utc)


def hours(actual=10, history=10):
    return {DAY-timedelta(days=d): {h:{'A':Decimal(actual if d==0 else history)} for h in range(24)} for d in range(10)}


class CurveMathTests(SimpleTestCase):
    def test_hourly_shape_is_not_replaced_with_daily_constant(self):
        data=hours()
        for d in data:
            for h in range(24):
                data[d][h]['A']=Decimal(5 if h<8 else 50)
        data[DAY][2]['A']=Decimal(15)
        result=evaluate_curve(DAY,data,{},30)
        self.assertEqual(result['anomaly_hours'],[2])
        self.assertEqual(result['curve'][12]['expected_kwh'],50)
        self.assertEqual(result['curve'][2]['expected_kwh'],5)

    def test_n_selects_latest_matching_days_and_excludes_future(self):
        data=hours(100,10)
        data[DAY+timedelta(days=1)]={0:{'A':Decimal(999)}}
        result=evaluate_curve(DAY,data,{'history_days':2},30)
        self.assertEqual(result['baseline_dates'],['2026-10-30','2026-11-02'])
        self.assertEqual(result['curve'][0]['sample_count'],2)
        self.assertEqual(result['curve'][0]['expected_kwh'],10)

    def test_weekends_and_same_weekday_are_separate(self):
        data=hours(10,10)
        for d in data:
            if d.weekday()>=5:
                for h in data[d]: data[d][h]['A']=Decimal(2)
        result=evaluate_curve(DAY,data,{},30)
        self.assertEqual(result['curve'][0]['expected_kwh'],10)
        self.assertTrue(same_group(date(2026,11,1),date(2026,10,31),'day_type'))
        self.assertFalse(same_group(DAY,date(2026,11,2),'weekday'))
        self.assertTrue(same_group(DAY,date(2026,11,1),'all'))

    def test_fixed_range_and_target_leakage(self):
        rule={'window_mode':'range','date_from':'2026-10-28','date_to':'2026-11-10','day_group':'all'}
        result=evaluate_curve(DAY,hours(1000),rule,30)
        self.assertTrue(all('2026-10-28'<=d<'2026-11-03' for d in result['baseline_dates']))
        self.assertEqual(result['curve'][0]['expected_kwh'],10)
        self.assertEqual(history_bounds(DAY,rule),(date(2026,10,28),DAY))

    def test_median_resists_single_outlier(self):
        data=hours()
        data[DAY-timedelta(days=1)][0]['A']=Decimal(1000)
        self.assertEqual(evaluate_curve(DAY,data,{'method':'median'},30)['curve'][0]['expected_kwh'],10)
        self.assertGreater(evaluate_curve(DAY,data,{},30)['curve'][0]['expected_kwh'],10)

    def test_linear_uses_actual_dates_and_needs_two_samples(self):
        data={DAY-timedelta(days=d):{h:{'A':Decimal(100-d*2)} for h in range(24)} for d in (0,1,4,8)}
        result=evaluate_curve(DAY,data,{'method':'linear','day_group':'all'},30)
        self.assertEqual(result['curve'][0]['expected_kwh'],100)
        self.assertEqual(result['curve'][0]['slope_kwh_per_day'],2)
        self.assertEqual(result['status'],'normal')
        self.assertEqual(evaluate_curve(DAY,data,{'method':'linear','history_days':1},30)['status'],'insufficient')

    def test_live_ignores_unfinished_and_future_hours(self):
        data=hours()
        data[DAY][5]['A']=Decimal(1000)
        result=evaluate_curve(DAY,data,{},30,5)
        self.assertEqual(result['status'],'normal')
        self.assertIsNone(result['curve'][5]['actual_kwh'])
        self.assertEqual(result['checked_hours'],5)
        self.assertEqual(result['energy_kwh'],50)
        self.assertEqual(evaluate_curve(DAY,data,{},30,0)['status'],'insufficient')

    def test_exact_threshold_both_directions_and_absolute_floor(self):
        for actual,status in ((13,'critical'),(7,'critical'),('12.999','normal')):
            self.assertEqual(evaluate_curve(DAY,hours(actual),{},30)['status'],status)
        self.assertEqual(evaluate_curve(DAY,hours(13),{'absolute_threshold':'4'},30)['status'],'normal')
        self.assertEqual(evaluate_curve(DAY,hours(14),{'absolute_threshold':'4'},30)['status'],'critical')

    def test_zero_signed_and_unknown_values(self):
        self.assertEqual(evaluate_curve(DAY,hours(0,0),{},30)['status'],'normal')
        zero=evaluate_curve(DAY,hours(1,0),{},30)
        self.assertEqual(zero['status'],'critical')
        self.assertIsNone(zero['curve'][0]['deviation_percent'])
        self.assertEqual(evaluate_curve(DAY,hours(1,0),{'absolute_threshold':'2'},30)['status'],'normal')
        self.assertEqual(evaluate_curve(DAY,hours(-13,-10),{},30)['status'],'critical')
        data=hours()
        data[DAY][0]['A']=None
        result=evaluate_curve(DAY,data,{},30)
        self.assertIsNone(result['energy_kwh'])
        self.assertEqual(result['status'],'insufficient')
        self.assertIsNone(complete_total({'A':Decimal('NaN')},{'A'}))
        self.assertIsNone(complete_total({'A':Decimal(1)},{'A','B'}))

    def test_minimum_samples_and_missing_meter_sets(self):
        data=hours()
        for d in data:
            if d!=DAY: data[d][0]['B']=Decimal(1)
        result=evaluate_curve(DAY,data,{'minimum_days':3},30)
        self.assertIsNone(result['curve'][0]['expected_kwh'])
        self.assertEqual(result['curve'][0]['sample_count'],0)
        self.assertEqual(result['status'],'insufficient')

    def test_validation_boundaries_and_default_independence(self):
        self.assertEqual(normalize_rule({}),DEFAULT_RULE)
        bad=[[],{'other':1},{'method':'eval(x)'},{'mode':'bad'},{'day_group':'holiday'},
             {'history_days':True},{'history_days':0},{'history_days':61},{'minimum_days':0},
             {'minimum_days':8},{'absolute_threshold':'NaN'},{'absolute_threshold':-1},
             {'window_mode':'range'},{'window_mode':'range','date_from':'x','date_to':'x'},
             {'window_mode':'range','date_from':'2026-11-04','date_to':'2026-11-03'},
             {'window_mode':'range','date_from':'2024-01-01','date_to':'2026-01-01'},
             {'window_mode':'range','date_from':'2026-01-01','date_to':'2026-01-02','minimum_days':3}]
        for rule in bad:
            with self.subTest(rule=rule),self.assertRaises(ValueError): normalize_rule(rule)
        for threshold in (0,-1,'NaN',10001,'bad','0.001'):
            with self.assertRaises(ValueError): evaluate_curve(DAY,{}, {},threshold)
        for closed in (-1,25,True):
            with self.assertRaises(ValueError): evaluate_curve(DAY,{}, {},30,closed)

    def test_random_constant_curves_do_not_generate_false_alarms(self):
        rng=random.Random(42)
        for _ in range(30):
            value=Decimal(rng.randrange(-1000,1000))
            self.assertEqual(evaluate_curve(DAY,hours(value,value),{},'0.01')['status'],'normal')


class CurvePreviewTests(SimpleTestCase):
    def test_dates_and_mode_validation(self):
        for day,rule in ((DAY+timedelta(days=5),{}),(DAY,{'mode':'live'}),(NOW.date(),{})):
            with self.assertRaises(ValueError): preview_curve(day,rule,30,NOW)

    @patch('apps.notifications.preview.load_live_hours')
    @patch('apps.notifications.preview.load_hours')
    def test_live_reads_source_and_excludes_current_hour(self, history, live):
        history.return_value=hours()
        today=NOW.date()
        live.return_value=({today:{h:{'A':Decimal(10)} for h in range(12)}},{'closed_hours':12,'as_of':NOW.isoformat()})
        result=preview_curve(today,{'mode':'live','day_group':'all'},30,NOW)
        self.assertEqual(result['closed_hours'],12)
        self.assertIsNone(result['curve'][12]['actual_kwh'])
        live.return_value=({today:{}},{'closed_hours':11})
        with self.assertRaises(ValueError): preview_curve(today,{'mode':'live'},30,NOW)

    def test_hourly_reader_rejects_duplicates_unknown_counts_and_nonfinite(self):
        cursor=MagicMock()
        stamp=NOW.replace(hour=0)
        cursor.fetchall.return_value=[(stamp,'A',Decimal(1),10,10),(stamp,'A',Decimal(1),10,10),
            (stamp,'B',Decimal('Infinity'),10,10),(stamp,'C',Decimal(0),10,10),(stamp,'D',Decimal(1),10,None)]
        with patch('apps.notifications.hourly.connections') as db:
            db.__getitem__.return_value.cursor.return_value.__enter__.return_value=cursor
            result=load_hours(DAY,DAY+timedelta(days=1))
        self.assertEqual(result[stamp.astimezone(__import__('zoneinfo').ZoneInfo('Europe/Moscow')).date()][3],
                         {'A':None,'B':None,'C':Decimal(0),'D':None})
        self.assertEqual(load_hours(DAY,DAY),{})


class CurveAPITests(TestCase):
    def setUp(self):
        self.user=User.objects.create_user('curve@example.invalid','test-password')
        self.client=APIClient()

    def test_preview_requires_authentication_and_never_writes_settings_or_reports(self):
        self.assertEqual(self.client.post('/api/notifications/preview/',{},format='json').status_code,401)
        self.client.force_authenticate(self.user)
        with patch('apps.notifications.views.preview_curve',return_value={'status':'normal'}):
            response=self.client.post('/api/notifications/preview/',{'day':DAY.isoformat(),'baseline':{}},format='json')
        self.assertEqual(response.status_code,200)
        self.assertFalse(NotificationPreference.objects.exists())
        self.assertFalse(DailyConsumptionReport.objects.exists())

    def test_api_validation_and_source_failure(self):
        self.client.force_authenticate(self.user)
        for body in ({},{'day':'no'},{'day':DAY.isoformat(),'baseline':{'history_days':0}},
                     {'day':DAY.isoformat(),'other':1},{'day':DAY.isoformat(),'threshold_percent':'0'}):
            self.assertEqual(self.client.post('/api/notifications/preview/',body,format='json').status_code,400)
        with patch('apps.notifications.views.preview_curve',side_effect=ValueError('Источник недоступен')):
            self.assertEqual(self.client.post('/api/notifications/preview/',{'day':DAY.isoformat()},format='json').status_code,400)

    def test_settings_handoff_prevents_old_worker_from_using_new_rules(self):
        self.client.force_authenticate(self.user)
        pref=NotificationPreference.objects.create(user=self.user,enabled=True)
        response=self.client.patch('/api/notifications/settings/',{'enabled':True,'baseline':{'mode':'live','history_days':2}},format='json')
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.data['enabled'])
        pref.refresh_from_db()
        self.assertFalse(pref.enabled)
        self.assertTrue(pref.rule_enabled)
        self.assertEqual(pref.baseline_config['mode'],'live')
        self.assertIsNone(pref.last_processed)
        response=self.client.patch('/api/notifications/settings/',{'enabled':False},format='json')
        self.assertFalse(response.data['enabled'])

    def test_distinct_rule_versions_keep_same_day_history(self):
        pref=NotificationPreference.objects.create(user=self.user)
        for key in ('','a','b'):
            DailyConsumptionReport.objects.create(user=self.user,day=DAY,threshold_percent=30,configuration_key=key)
        self.client.force_authenticate(self.user)
        response=self.client.get('/api/notifications/reports/')
        self.assertEqual(response.data['count'],3)
        self.assertIn('baseline_snapshot',response.data['results'][0])


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',EMAIL_HOST='test-only',DEFAULT_FROM_EMAIL='test@example.invalid')
class CurveWorkerTests(TestCase):
    def setUp(self):
        self.user=User.objects.create_user('curve-worker@example.invalid','test-password')
        self.rule=normalize_rule({})
        self.pref=NotificationPreference.objects.create(user=self.user,rule_enabled=True,baseline_config=self.rule,monitor_from=DAY)
        self.result=evaluate_curve(DAY,hours(20),self.rule,30)
        mail.outbox=[]

    def cycle(self,result=None,now=NOW):
        with patch('apps.notifications.curve_service.preview_curve',return_value=result or self.result):
            run_cycle(now)

    def test_daily_once_and_email_uses_curve_not_constant(self):
        self.cycle();self.cycle()
        report=DailyConsumptionReport.objects.get()
        self.assertEqual(report.status,'critical')
        self.assertEqual(len(mail.outbox),1)
        self.assertIn('почасовой кривой',mail.outbox[0].body)
        self.assertEqual(report.delivery_snapshot['anomaly_hours'],list(range(24)))

    def test_live_updates_report_but_sends_once(self):
        self.pref.baseline_config=normalize_rule({'mode':'live'});self.pref.save()
        result={**self.result,'day':NOW.date().isoformat(),'rule':self.pref.baseline_config}
        self.cycle(result)
        first=DailyConsumptionReport.objects.get().delivery_snapshot
        result={**result,'checked_hours':13,'explanation':'Новые данные'}
        self.cycle(result)
        report=DailyConsumptionReport.objects.get()
        self.assertEqual(report.baseline_snapshot['checked_hours'],13)
        self.assertEqual(report.delivery_snapshot,first)
        self.assertEqual(len(mail.outbox),1)

    def test_disabled_source_failure_and_rule_changes_during_calculation(self):
        with patch('apps.notifications.curve_service.preview_curve',side_effect=ValueError('unavailable')):
            run_cycle(NOW)
        self.assertFalse(DailyConsumptionReport.objects.exists())
        def disable(*_):
            NotificationPreference.objects.filter(pk=self.pref.pk).update(rule_enabled=False)
            return self.result
        with patch('apps.notifications.curve_service.preview_curve',side_effect=disable): run_cycle(NOW)
        self.assertFalse(DailyConsumptionReport.objects.exists())
        self.assertEqual(len(mail.outbox),0)

    def test_previous_pending_rule_is_not_sent_after_reconfiguration(self):
        self.cycle()
        report=DailyConsumptionReport.objects.get()
        report.email_status='pending';report.save()
        self.pref.baseline_config=normalize_rule({'history_days':2});self.pref.save()
        deliver_report(report.pk)
        self.assertEqual(len(mail.outbox),1)

    def test_insufficient_can_recover_and_normal_never_sends(self):
        insufficient=evaluate_curve(DAY,{},self.rule,30)
        self.cycle(insufficient)
        self.assertEqual(DailyConsumptionReport.objects.get().status,'insufficient')
        self.cycle(evaluate_curve(DAY,hours(),self.rule,30))
        self.assertEqual(DailyConsumptionReport.objects.get().status,'normal')
        self.assertEqual(len(mail.outbox),0)

class CurveReaderIntegrationTests(TestCase):
    databases={'default','analytics'}

    def setUp(self):
        from django.db import connections
        self.user=User.objects.create_user('reader-integration@example.invalid','test-password')
        self.client=APIClient();self.client.force_authenticate(self.user)
        with connections['analytics'].cursor() as cursor:
            cursor.execute('CREATE TABLE public.curve_test_hourly(bucket_start timestamptz,sensor_name text,energy_kwh numeric,points_count bigint,active_power_w_avg_count bigint)')
            for offset in range(3):
                for hour in range(24):
                    from zoneinfo import ZoneInfo
                    stamp=datetime.combine(DAY-timedelta(days=offset),datetime.min.time(),ZoneInfo('Europe/Moscow'))+timedelta(hours=hour)
                    cursor.execute('INSERT INTO public.curve_test_hourly VALUES (%s,%s,%s,720,720)',
                                   [stamp,'A',20 if offset==0 else 10])

    @override_settings(ENERGY_HOURLY_TABLE='public.curve_test_hourly')
    def test_real_hourly_query_preview_and_dates_without_mutations(self):
        response=self.client.post('/api/notifications/preview/',
            {'day':DAY.isoformat(),'baseline':{'history_days':2,'day_group':'all'}},format='json')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.data['curve'][0]['expected_kwh'],10)
        self.assertEqual(response.data['status'],'critical')
        self.assertEqual(len(response.data['curve']),24)
        response=self.client.get('/api/notifications/preview/')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.data['last_day'],DAY)
        self.assertFalse(NotificationPreference.objects.exists())
        self.assertFalse(DailyConsumptionReport.objects.exists())


class CurveAdditionalTests(SimpleTestCase):
    def test_degenerate_trend_and_future_only_range(self):
        from .curve import fitted_value
        self.assertEqual(fitted_value([(DAY,Decimal(1)),(DAY,Decimal(2))],DAY,'linear'),(None,None))
        rule={'window_mode':'range','date_from':'2026-11-10','date_to':'2026-11-11'}
        self.assertEqual(evaluate_curve(DAY,hours(),rule,30)['status'],'insufficient')

    def test_live_reader_formats_response_and_surfaces_errors(self):
        from .hourly import load_live_hours, hourly_table
        with patch('apps.notifications.hourly.request_adapter',return_value=({'detail':'unavailable'},400)):
            with self.assertRaises(ValueError):load_live_hours()
        with patch('apps.notifications.hourly.request_adapter',return_value=({'day':DAY.isoformat(),'closed_hours':1,
            'rows':[{'hour':0,'sensor_name':'A','energy_kwh':'0'},{'hour':0,'sensor_name':'B','energy_kwh':None}]},200)):
            result,_=load_live_hours()
            self.assertEqual(result[DAY][0],{'A':Decimal(0),'B':None})
        with override_settings(ENERGY_HOURLY_TABLE='x;drop.bad'):
            with self.assertRaises(ValueError):hourly_table()

    def test_command_separate_locks_once_and_releases_on_errors(self):
        from .management.commands.run_notifications import Command, run_locked
        with patch('apps.notifications.management.commands.run_notifications.connection') as conn,\
             patch('apps.notifications.management.commands.run_notifications.connections') as connections:
            conn.cursor.return_value.__enter__.return_value.fetchone.return_value=(False,)
            callback=MagicMock()
            self.assertFalse(run_locked(callback,1));callback.assert_not_called()
            conn.cursor.return_value.__enter__.return_value.fetchone.return_value=(True,)
            self.assertTrue(run_locked(callback,2));callback.assert_called_once()
            callback.side_effect=ValueError('test')
            with self.assertRaises(ValueError):run_locked(callback,3)
            connections.close_all.assert_called()
        with patch('apps.notifications.management.commands.run_notifications.run_locked') as locked,\
             patch('apps.notifications.management.commands.run_notifications.signal.signal'),\
             patch('apps.notifications.management.commands.run_notifications.Path') as path:
            Command().handle(once=True,interval=60)
            self.assertEqual([c.args[1] for c in locked.call_args_list],[482019773,482019774])
            path.return_value.touch.assert_called_once()
        with patch('apps.notifications.management.commands.run_notifications.run_locked',side_effect=ValueError()),\
             patch('apps.notifications.management.commands.run_notifications.signal.signal'):
            with self.assertRaises(ValueError):Command().handle(once=True,interval=60)
