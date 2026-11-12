from concurrent.futures import Future
from datetime import datetime, timedelta
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from adapter.availability import bounds, classify, ReportCache, read_report, ZONE, DAY_STATS
from adapter.mapping import validate_profile
from adapter.runner import fingerprint
from adapter.service import build_settings
from adapter.tests.test_adapter import PROFILE


class ClassificationTests(unittest.TestCase):
    def setUp(self):
        self.start, self.end, self.history = bounds('day', datetime(2026, 11, 5, 12, tzinfo=ZONE))
        self.devices = [dict(id=i, sensor_name=str(i), model_id=1, kind='electricity') for i in range(1, 5)]
        self.stats = {str(i): {self.history.date()+timedelta(days=n): {'valid':100, 'records':110, 'cadence':5}
                              for n in range(30)} for i in range(1, 5)}

    def test_exact_threshold_and_unknown_reference(self):
        for i, count in enumerate((90, 89, 0, 110), 1):
            self.stats[str(i)][self.start.date()] = {'valid':count,'records':110}
        rows, summary = classify(self.devices, self.stats, self.start, self.end)
        by_id = {row['id']:row for row in rows}
        self.assertEqual(by_id[1]['status'], 'ok')
        self.assertEqual(by_id[2]['status'], 'low_data')
        self.assertEqual(by_id[3]['status'], 'no_data')
        self.assertEqual(by_id[4]['status'], 'ok')
        self.assertEqual(summary, {'total':4,'no_data':1,'low_data':1,'unknown':0,'ok':2})

    def test_new_device_cannot_normalize_itself_using_current_period(self):
        self.stats = {'1':{self.start.date():{'valid':300,'records':300}}}
        rows, _ = classify(self.devices[:1],self.stats,self.start,self.end)
        self.assertEqual(rows[0]['status'], 'unknown')
        self.assertIsNone(rows[0]['reference_records'])

    def test_different_models_and_cadences_do_not_share_reference(self):
        self.devices[-1]['model_id'] = 2
        for day in self.stats['3'].values():
            day['cadence'] = 60
            day['valid'] = 1000
        for i in range(1, 5):
            self.stats[str(i)][self.start.date()] = {'valid':90, 'records':100}
        rows, _ = classify(self.devices,self.stats,self.start,self.end)
        for row in rows:
            self.assertEqual(row['reference_method'], 'own_history')
        self.assertEqual(next(r for r in rows if r['id']==1)['reference_records'], 100)

    def test_failed_source_never_becomes_no_data(self):
        rows,_ = classify(self.devices,self.stats,self.start,self.end,{'electricity':'unavailable'})
        self.assertTrue(all(row['status']=='unknown' for row in rows))
        self.assertTrue(all(row['valid_records'] is None for row in rows))

    def test_minimum_stable_history_and_outlier_resistance(self):
        for i in (1,2,3):
            counts=list(self.stats[str(i)].values())
            for entry in counts[:9]:
                entry['valid']=0
        self.stats['4'] = {day:{**entry,'valid':100000} for day,entry in self.stats['4'].items()}
        self.stats['1'][self.start.date()]={'valid':90,'records':90}
        rows,_=classify(self.devices,self.stats,self.start,self.end)
        row=next(r for r in rows if r['id']==1)
        self.assertEqual(row['reference_records'],100)
        self.assertEqual(row['status'],'ok')
        for entry in list(self.stats['1'].values())[9:10]:
            entry['valid']=0
        rows,_=classify(self.devices[:1],self.stats,self.start,self.end)
        self.assertEqual(rows[0]['status'],'unknown')

    def test_moscow_boundaries(self):
        start,end,history=bounds('month',datetime.fromisoformat('2026-11-04T22:00:00+00:00'))
        self.assertEqual(start.isoformat(),'2026-10-06T00:00:00+03:00')
        self.assertEqual(end.isoformat(),'2026-11-05T00:00:00+03:00')
        self.assertEqual((start-history).days,30)
        with self.assertRaises(ValueError):
            bounds('all')


class CacheTests(unittest.TestCase):
    def test_coalesces_requests_and_bounds_reader_work(self):
        cache=ReportCache()
        cache.pool.shutdown()
        pending=Future()
        class Pool:
            calls=0
            def submit(self,*args,**kwargs):
                self.calls+=1
                return pending
        cache.pool=Pool()
        controller=SimpleNamespace(store=SimpleNamespace(schema='test'))
        self.assertEqual(cache.get(controller,'day')['state'],'pending')
        self.assertEqual(cache.get(controller,'day')['state'],'pending')
        self.assertEqual(cache.pool.calls,1)
        pending.set_result({'state':'ready','rows':[]})
        self.assertEqual(cache.get(controller,'day')['state'],'ready')
        self.assertEqual(cache.get(controller,'day',refresh=True)['state'],'ready')
        self.assertEqual(cache.pool.calls,1)


@unittest.skipUnless(os.environ.get('ADAPTER_TEST_ADMIN_DSN'), 'Isolated PostgreSQL required')
class SourceReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        admin=os.environ['ADAPTER_TEST_ADMIN_DSN']
        with psycopg.connect(admin,autocommit=True) as conn:
            if not conn.execute("SELECT 1 FROM pg_database WHERE datname='test_availability'").fetchone():
                conn.execute('CREATE DATABASE test_availability')
        cls.dsn=make_conninfo(admin,dbname='test_availability')

    def setUp(self):
        DAY_STATS.clear()
        with psycopg.connect(self.dsn) as conn:
            conn.execute('''DROP SCHEMA IF EXISTS structure CASCADE; CREATE SCHEMA structure;
                DROP TABLE IF EXISTS sensor_directory,device_list,device_type,readings,climate,meteo;
                CREATE TABLE structure.room(id bigint PRIMARY KEY,room_number text);
                CREATE TABLE device_type(id bigint PRIMARY KEY,type text);
                CREATE TABLE device_list(id bigint PRIMARY KEY,device_typeid bigint,device_name text);
                CREATE TABLE sensor_directory(id bigint PRIMARY KEY,sensor_name text,device_listid bigint,roomid bigint);
                CREATE TABLE readings(ts timestamptz,sensor_name text,pt numeric,p1 numeric,p2 numeric,p3 numeric);
                CREATE TABLE climate(ts timestamp,sensor_name text,temperature double precision,humidity numeric,motion boolean);
                CREATE TABLE meteo(ts timestamptz,sensor_name text,temperature numeric,windspeed numeric);''')
            with conn.cursor() as cur:
                cur.executemany('INSERT INTO device_type VALUES(%s,%s)',[(1,'Электросчетчик'),(2,'Климатический датчик'),(3,'Метеостанция'),(4,'Неизвестный')])
                cur.executemany('INSERT INTO device_list VALUES(%s,%s,%s)',[(1,1,'Meter'),(2,2,'Climate'),(3,3,'Meteo'),(4,4,'Unknown')])
                cur.executemany('INSERT INTO sensor_directory VALUES(%s,%s,%s,NULL)',[(1,'a',1),(2,'b',1),(3,'c',1),(4,'climate',2),(5,'meteo',3),(6,'silent',1),(7,'other',4)])
            # Baseline: thirty days, three unique measurements per device per day.
            for table,names,value in (('readings',['a','b','c'],'pt'),('climate',['climate'],'temperature'),('meteo',['meteo'],'temperature')):
                for name in names:
                    conn.execute(sql.SQL("INSERT INTO {} (ts,sensor_name,{}) SELECT ts,%s,10 FROM generate_series(timestamp '2026-10-05 09:00',timestamp '2026-11-03 09:00',interval '1 day') day CROSS JOIN LATERAL generate_series(day,day+interval '2 minutes',interval '1 minute') ts").format(sql.Identifier(table),sql.Identifier(value)),(name,))
            conn.execute("""INSERT INTO readings VALUES
              ('2026-11-04 09:00+03','a',10,NULL,NULL,NULL),('2026-11-04 09:01+03','a',10,NULL,NULL,NULL),('2026-11-04 09:02+03','a',10,NULL,NULL,NULL),
              ('2026-11-04 09:00+03','b',10,NULL,NULL,NULL),('2026-11-04 09:00+03','b',10,NULL,NULL,NULL),('2026-11-04 09:01+03','b',0,NULL,NULL,NULL),
              ('2026-11-04 09:02+03','b',NULL,NULL,NULL,NULL),('2026-11-04 09:00+03','c',NULL,10,NULL,NULL),('2026-11-04 09:00+03','c',NULL,NULL,10,10);
              INSERT INTO climate VALUES('2026-11-04 09:00','climate',0,0,false),('2026-11-04 09:00','climate',5,NULL,false),('2026-11-04 09:01','climate','Infinity',NULL,false);
              INSERT INTO meteo VALUES('2026-11-04 09:00+03','meteo',NULL,0),('2026-11-04 09:01+03','meteo',-5,0);""")
        self.doc=validate_profile({**PROFILE,'source_table':'public.readings','confirmed':True,
            'columns':{'ts':'ts','sensor_name':'sensor_name','pt':'pt','p1':'p1','p2':'p2','p3':'p3'},
            'scales':{},'sample_seconds':60})
        self.environment={'ENERGY_AVAILABILITY_PROFILE_ID':'test','ENERGY_AGG_TARGET_DSN':self.dsn,
            'ENERGY_AVAILABILITY_SOURCES':'{"climate":{"table":"public.climate"},"meteo":{"table":"public.meteo"}}'}
        with patch.dict(os.environ,self.environment):
            signature=fingerprint(build_settings(self.doc,{'paradigm':self.dsn}))
        self.profile={'id':'test','document':self.doc,'signature':signature,'validated':True}
        self.controller=SimpleNamespace(store=SimpleNamespace(profile=lambda _id:self.profile),registry={'paradigm':self.dsn})

    def report(self):
        with patch.dict(os.environ,self.environment):
            return read_report(self.controller,'day',now=datetime(2026,11,5,12,tzinfo=ZONE))

    def test_all_types_deduplication_zero_null_and_read_only(self):
        report=self.report()
        rows={r['sensor_name']:r for r in report['rows']}
        self.assertEqual(report['summary']['total'],7)
        self.assertEqual(rows['a']['status'],'ok')
        self.assertEqual(rows['b']['valid_records'],1)
        self.assertEqual(rows['b']['records'],3)
        self.assertEqual(rows['b']['status'],'low_data')
        self.assertEqual(rows['c']['status'],'no_data')
        self.assertEqual(rows['climate']['valid_records'],1)
        self.assertEqual(rows['climate']['reference_method'],'own_history')
        self.assertEqual(rows['meteo']['valid_records'],1)
        self.assertEqual(rows['silent']['status'],'no_data')
        self.assertEqual(rows['other']['status'],'unknown')
        with psycopg.connect(self.dsn) as conn:
            self.assertIsNone(conn.execute("SELECT to_regclass('student_schema.electricity_sensor_readings_daily')").fetchone()[0])
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM readings').fetchone()[0],279)

    def test_one_failed_source_is_unknown_not_no_data(self):
        with psycopg.connect(self.dsn) as conn:
            conn.execute('ALTER TABLE meteo ALTER COLUMN temperature TYPE text')
        report=self.report()
        self.assertEqual(report['state'],'partial')
        rows={r['sensor_name']:r for r in report['rows']}
        self.assertEqual(rows['meteo']['status'],'unknown')
        self.assertEqual(rows['a']['status'],'ok')

    def test_daily_cache_reuses_statistics_without_changing_result(self):
        first=self.report()
        second=self.report()
        self.assertEqual(second['rows'],first['rows'])
        self.assertEqual(first['queried_days'],93)
        self.assertEqual(second['queried_days'],0)
        self.assertEqual(second['reused_days'],93)
        with patch.dict(os.environ,self.environment):
            refreshed=read_report(self.controller,'day',now=datetime(2026,11,5,12,tzinfo=ZONE),reuse_daily=False)
        self.assertEqual(refreshed['reused_days'],0)
        self.assertEqual(refreshed['rows'],first['rows'])

    def test_unvalidated_profile_is_rejected_without_source_scan(self):
        self.profile['validated']=False
        with self.assertRaises(ValueError):
            self.report()

    def test_different_periods_reuse_overlapping_days(self):
        self.report()
        with patch.dict(os.environ,self.environment):
            weekly=read_report(self.controller,'week',now=datetime(2026,11,5,12,tzinfo=ZONE))
        self.assertEqual(weekly['queried_days'],18)
        self.assertEqual(weekly['reused_days'],93)
