from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

from adapter.live import _CACHE, live_hours

NOW=datetime(2026,11,3,9,40,tzinfo=timezone.utc)


class LiveHoursTests(TestCase):
    def setUp(self):
        _CACHE.clear()
        self.document={'connection':'paradigm','source_table':'public.readings','columns':{'ts':'ts','sensor_name':'sensor_name','pt':'pt'},
            'quality':{'min_coverage':0.9,'max_duplicate_fraction':0.001},'sensor_map':{}}
        self.controller=SimpleNamespace(store=MagicMock(),registry={'paradigm':'dbname=test'})
        self.controller.store.query.return_value={'id':'test-profile','document':self.document,'signature':'approved'}
        self.settings=SimpleNamespace(source_dsn='dbname=test',source_table='public.readings',profile=self.document,sample_seconds=Decimal(5))

    def run_live(self, rows, now=NOW):
        with patch('adapter.service.build_settings',return_value=self.settings),patch('adapter.live.psycopg.connect') as connect,\
             patch('adapter.live.column_types',return_value={}),patch('adapter.live.source_capabilities',return_value={'available_metrics':['active_power_w_avg']}),\
             patch('adapter.live.source_cte'),patch('adapter.live.hourly_query'),patch('adapter.live.fingerprint',return_value='approved'):
            conn=connect.return_value.__enter__.return_value
            conn.execute.return_value.fetchall.return_value=rows
            result=live_hours(self.controller,now)
            return result,conn

    def row(self, points=720, valid=720):
        return {'bucket_start':NOW.replace(hour=8,minute=0),'sensor_name':'A','points_count':points,
            'active_power_w_avg_count':valid,'unique_points_count':points,'active_power_w_avg_sum':Decimal(720000),
            'power_seconds_sum':Decimal(5000)*valid,'power_seconds_count':valid*5}

    def test_closed_hours_only_readonly_and_energy(self):
        result,conn=self.run_live([self.row()])
        self.assertEqual(result['closed_hours'],12)
        self.assertEqual(Decimal(result['rows'][0]['energy_kwh']),1)
        self.assertIn('SET default_transaction_read_only=on',[c.args[0] for c in conn.execute.call_args_list if isinstance(c.args[0],str)])
        self.assertEqual(conn.execute.call_args.args[1]['end'].hour,12)
        self.assertEqual(conn.execute.call_args.args[1]['end'].minute,0)

    def test_invalid_coverage_is_unknown_not_zero(self):
        result,_=self.run_live([self.row(100,100)])
        self.assertIsNone(result['rows'][0]['energy_kwh'])

    def test_cache_and_midnight_do_not_repeat_large_queries(self):
        first,_=self.run_live([self.row()])
        with patch('adapter.live.psycopg.connect') as connect, patch('adapter.service.build_settings',return_value=self.settings), patch('adapter.live.fingerprint',return_value='approved'):
            self.assertEqual(live_hours(self.controller,NOW),first)
            connect.assert_not_called()
        _CACHE.clear()
        result,conn=self.run_live([],datetime(2026,11,2,21,30,tzinfo=timezone.utc))
        self.assertEqual(result['closed_hours'],0)
        self.assertEqual(result['rows'],[])
        conn.execute.assert_not_called()

    def test_missing_profile_and_merged_meter_ids_fail(self):
        self.controller.store.query.return_value=None
        with patch.dict('os.environ',{'ENERGY_NOTIFICATION_FALLBACK_SCHEMA':''}),self.assertRaises(ValueError):
            live_hours(self.controller,NOW)
        self.controller.store.query.return_value={'id':'test','document':self.document,'signature':'approved'}
        self.document['sensor_map']={'A':'same','B':'same'}
        second={**self.row(),'sensor_name':'B'}
        with self.assertRaises(ValueError):self.run_live([self.row(),second])

    def test_merged_duplicates_use_only_unique_time_support(self):
        row={**self.row(),'unique_points_count':719}
        result,_=self.run_live([row])
        self.assertEqual(Decimal(result['rows'][0]['energy_kwh']),1)
