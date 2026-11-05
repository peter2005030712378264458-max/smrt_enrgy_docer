"""Connection secrets, validation and API boundaries without external databases."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet
import psycopg
from psycopg.conninfo import conninfo_to_dict

from adapter.connections import add_connection, check_connection, connection_document, saved_connections
from adapter.service import Controller, connections


BODY = {'alias': 'new-source', 'host': 'db.example.org', 'port': 5432, 'database': 'readings',
        'user': 'reader', 'password': "secret' with spaces", 'sslmode': 'require'}


class ConnectionTests(unittest.TestCase):
    def test_validation_and_readonly_dsn(self):
        alias, dsn = connection_document(BODY)
        values = conninfo_to_dict(dsn)
        self.assertEqual(alias, 'new-source')
        self.assertEqual(values['password'], BODY['password'])
        self.assertIn('default_transaction_read_only=on', values['options'])
        for changed in ({'alias': 'paradigm'}, {'host': '/tmp/socket'}, {'port': True},
                        {'port': 70000}, {'sslmode': 'bad'}, {'dsn': 'arbitrary'}, {'password': ''}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                connection_document({**BODY, **changed})

    @patch('adapter.connections.psycopg.connect', side_effect=psycopg.OperationalError("password=" + BODY['password']))
    def test_database_error_does_not_disclose_credentials(self, _):
        with self.assertRaises(ValueError) as error:
            check_connection(BODY)
        self.assertNotIn(BODY['password'], str(error.exception))

    @patch('adapter.connections.check_connection')
    def test_encrypted_persistence_restart_and_duplicate_alias(self, check):
        check.return_value = connection_document(BODY)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key = root/'key'
            key.write_bytes(Fernet.generate_key())
            registry = root/'registry.enc'
            env = {'ENERGY_ADAPTER_CONNECTIONS_FILE': str(registry),
                   'ENERGY_ADAPTER_CONNECTION_KEY_FILE': str(key),
                   'ENERGY_ADAPTER_CONNECTIONS': '{}', 'ENERGY_AGG_SOURCE_DSN': 'dbname=old'}
            with patch.dict(os.environ, env):
                controller = Controller(None, {'paradigm': 'dbname=old'})
                response = controller.dispatch('POST', '/connections', BODY)
                self.assertEqual(response, {'alias': 'new-source', 'status': 'saved'})
                self.assertEqual(controller.dispatch('GET', '/connections', {}), ['new-source', 'paradigm'])
                self.assertNotIn(BODY['password'].encode(), registry.read_bytes())
                self.assertNotIn(BODY['host'].encode(), registry.read_bytes())
                self.assertEqual(conninfo_to_dict(connections()['new-source'])['password'], BODY['password'])
                with self.assertRaises(ValueError):
                    add_connection(BODY, connections())
                key.write_bytes(Fernet.generate_key())
                with self.assertRaises(ValueError):
                    saved_connections()

    @patch('adapter.connections.psycopg.connect')
    def test_probe_uses_readonly_session(self, connect):
        conn = connect.return_value.__enter__.return_value
        conn.execute.return_value.fetchone.return_value = ('on',)
        alias, _ = check_connection(BODY)
        self.assertEqual(alias, BODY['alias'])
        self.assertEqual(conn.execute.call_args.args[0], 'SHOW transaction_read_only')

    def test_initial_registry_can_be_empty_when_management_is_configured(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {'ENERGY_ADAPTER_CONNECTIONS': '{}', 'ENERGY_AGG_SOURCE_DSN': '',
                                         'ENERGY_AGG_DIRECTORY_DSN': '',
                                         'ENERGY_ADAPTER_CONNECTIONS_FILE': str(Path(directory)/'connections')}):
                self.assertEqual(connections(), {})

@unittest.skipUnless(os.environ.get('ADAPTER_TEST_ADMIN_DSN'), 'Isolated PostgreSQL not configured')
class ConnectionIntegrationTests(unittest.TestCase):
    def test_real_connection_discovery_and_namespace_isolation(self):
        import uuid
        from psycopg import sql
        from adapter.store import Store
        from adapter.mapping import inspect_source
        admin = os.environ['ADAPTER_TEST_ADMIN_DSN']
        info = conninfo_to_dict(admin)
        body = {**BODY, 'host': info['host'], 'database': info.get('dbname', 'postgres'),
                'user': info['user'], 'password': info['password'], 'sslmode': 'disable'}
        schemas = ['connection_test_' + uuid.uuid4().hex[:12] for _ in range(2)]
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory)/'key'
            key.write_bytes(Fernet.generate_key())
            with patch.dict(os.environ, {'ENERGY_ADAPTER_CONNECTIONS_FILE': str(Path(directory)/'registry'),
                                         'ENERGY_ADAPTER_CONNECTION_KEY_FILE': str(key)}):
                controller = Controller(None, {})
                controller.dispatch('POST', '/connections', body)
                with psycopg.connect(admin, autocommit=True) as conn:
                    conn.execute('CREATE TABLE public.connection_test_readings (measured_at timestamptz, device_id text, active_power numeric)')
                try:
                    tables = controller.dispatch('GET', '/tables?connection=new-source', {})
                    self.assertIn('public.connection_test_readings', [t['name'] for t in tables])
                    with psycopg.connect(controller.registry['new-source'], autocommit=True, row_factory=psycopg.rows.dict_row) as conn:
                        inspection = inspect_source(conn, 'public.connection_test_readings')
                        self.assertEqual(inspection['suggestions']['ts']['column'], 'measured_at')
                        self.assertEqual(inspection['suggestions']['pt']['column'], 'active_power')
                        with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
                            conn.execute('INSERT INTO public.connection_test_readings VALUES (now(),\'x\',1)')
                    stores = []
                    for schema in schemas:
                        with patch.dict(os.environ, {'ENERGY_ADAPTER_CONTROL_SCHEMA': schema}):
                            store = Store(admin)
                        store.install()
                        stores.append(store)
                    profile = stores[0].query('INSERT INTO profiles(id, document) VALUES(%s,%s::jsonb) RETURNING *',
                                             ('profile', json.dumps({'name':'test'})), one=True)
                    stores[0].enqueue(profile['id'], 'inspect')
                    self.assertIsNone(stores[1].claim())
                    self.assertEqual(stores[0].claim()['profile_id'], 'profile')
                finally:
                    with psycopg.connect(admin, autocommit=True) as conn:
                        conn.execute('DROP TABLE public.connection_test_readings')
                        for schema in schemas:
                            conn.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(schema)))
