"""Control data only. Aggregate schemas are never migrated by the service."""
from contextlib import contextmanager
import uuid
import os
import re
from psycopg import sql

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

DDL = '''
CREATE SCHEMA IF NOT EXISTS adapter_control;
CREATE TABLE IF NOT EXISTS adapter_control.profiles (
 id text PRIMARY KEY, document jsonb NOT NULL, validated boolean NOT NULL DEFAULT false,
 active boolean NOT NULL DEFAULT false, processed_until date, signature text,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS adapter_one_active_profile ON adapter_control.profiles(active) WHERE active;
CREATE TABLE IF NOT EXISTS adapter_control.jobs (
 id text PRIMARY KEY, profile_id text NOT NULL REFERENCES adapter_control.profiles(id),
 kind text NOT NULL CHECK(kind IN ('inspect','preview','apply','scheduled')),
 status text NOT NULL DEFAULT 'queued', date_from date, date_to date,
 preview_id text REFERENCES adapter_control.jobs(id), slot text UNIQUE,
 events jsonb NOT NULL DEFAULT '[]', error text, attempts integer NOT NULL DEFAULT 0,
 available_at timestamptz NOT NULL DEFAULT now(), created_at timestamptz NOT NULL DEFAULT now(),
 started_at timestamptz, finished_at timestamptz
);
CREATE TABLE IF NOT EXISTS adapter_control.settings (
 id integer PRIMARY KEY CHECK(id=1), enabled boolean NOT NULL DEFAULT false,
 repeat_minutes integer NOT NULL DEFAULT 15 CHECK(repeat_minutes BETWEEN 0 AND 1439),
    schema_version integer NOT NULL DEFAULT 1
);
INSERT INTO adapter_control.settings(id) VALUES(1) ON CONFLICT DO NOTHING;
ALTER TABLE adapter_control.settings ADD COLUMN IF NOT EXISTS schema_version integer NOT NULL DEFAULT 1;
ALTER TABLE adapter_control.profiles ADD COLUMN IF NOT EXISTS signature text;
ALTER TABLE adapter_control.profiles ADD COLUMN IF NOT EXISTS attempted_until date;
CREATE TABLE IF NOT EXISTS adapter_control.day_results (
 profile_id text NOT NULL REFERENCES adapter_control.profiles(id), day date NOT NULL,
 outcome text NOT NULL CHECK(outcome IN ('written','skipped')),
 job_id text NOT NULL REFERENCES adapter_control.jobs(id),
 hourly_rows integer NOT NULL DEFAULT 0, daily_rows integer NOT NULL DEFAULT 0,
 reason text, recorded_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(profile_id,day)
);
UPDATE adapter_control.settings SET schema_version=2 WHERE id=1 AND schema_version=1;
'''


class Store:
    def __init__(self, dsn):
        self.dsn = dsn
        self.schema = os.environ.get('ENERGY_ADAPTER_CONTROL_SCHEMA', 'adapter_control')
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', self.schema):
            raise ValueError('Некорректная служебная схема адаптера')

    @contextmanager
    def connection(self):
        with psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=8) as conn:
            conn.execute("SET statement_timeout='15s'")
            conn.execute(sql.SQL('SET LOCAL search_path TO {}, pg_catalog').format(sql.Identifier(self.schema)))
            yield conn

    def query(self, text, params=(), one=False):
        with self.connection() as conn:
            cursor = conn.execute(text, params)
            if cursor.description:
                return cursor.fetchone() if one else cursor.fetchall()

    def install(self):
        with self.connection() as conn:
            version = conn.execute('SELECT to_regclass(%s) AS relation', (self.schema+'.settings',)).fetchone()
            if version['relation']:
                current = conn.execute('SELECT schema_version FROM settings WHERE id=1').fetchone()
                if current and current['schema_version'] not in (1, 2):
                    raise ValueError('Unsupported control schema version')
            conn.execute(DDL.replace('adapter_control', self.schema))

    def record_day(self, profile_id, job_id, event, scheduled=False):
        from datetime import date, timedelta
        day = date.fromisoformat(event['date'])
        outcome = 'written' if event['event'] == 'day' else 'skipped'
        with self.connection() as conn:
            profile = conn.execute('SELECT * FROM profiles WHERE id=%s FOR UPDATE', (profile_id,)).fetchone()
            conn.execute('''INSERT INTO day_results(profile_id,day,outcome,job_id,hourly_rows,daily_rows,reason)
                VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(profile_id,day) DO UPDATE SET
                outcome=EXCLUDED.outcome,job_id=EXCLUDED.job_id,hourly_rows=EXCLUDED.hourly_rows,
                daily_rows=EXCLUDED.daily_rows,reason=EXCLUDED.reason,recorded_at=now()''',
                (profile_id, day, outcome, job_id, event.get('hourly_rows', 0),
                 event.get('daily_rows', 0), event.get('reason')))
            attempted = profile['attempted_until'] or profile['processed_until']
            processed = profile['processed_until']
            if scheduled:
                attempted = attempted or day
                processed = processed or day
                if attempted == day:
                    attempted = day + timedelta(days=1)
            # A manual repair can close an old gap, but does not initialize or
            # move the automatic scanning boundary.
            if processed is not None and attempted is not None:
                while processed < attempted:
                    result = conn.execute('SELECT outcome FROM day_results WHERE profile_id=%s AND day=%s',
                                          (profile_id, processed)).fetchone()
                    if not result or result['outcome'] != 'written':
                        break
                    processed += timedelta(days=1)
            conn.execute('UPDATE profiles SET processed_until=%s,attempted_until=%s WHERE id=%s',
                         (processed, attempted, profile_id))

    def profile(self, profile_id):
        row = self.query('SELECT * FROM profiles WHERE id=%s', (profile_id,), one=True)
        if not row:
            raise ValueError('Профиль не найден')
        return row

    def job(self, job_id):
        row = self.query('SELECT * FROM jobs WHERE id=%s', (job_id,), one=True)
        if not row:
            raise ValueError('Задание не найдено')
        return row

    def enqueue(self, profile_id, kind, begin=None, end=None, preview_id=None, slot=None):
        job_id = str(uuid.uuid4())
        with self.connection() as conn:
            if kind == 'preview':
                conn.execute('UPDATE profiles SET validated=false WHERE id=%s', (profile_id,))
            return conn.execute('''INSERT INTO jobs(id,profile_id,kind,date_from,date_to,preview_id,slot)
                VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(slot) DO NOTHING RETURNING *''',
                (job_id, profile_id, kind, begin, end, preview_id, slot)).fetchone()

    def event(self, job_id, event):
        self.query('''UPDATE jobs SET events=(
            SELECT COALESCE(jsonb_agg(value ORDER BY n),'[]'::jsonb) FROM
            (SELECT value,n FROM jsonb_array_elements(events || %s::jsonb) WITH ORDINALITY AS e(value,n)
             ORDER BY n DESC LIMIT 800) recent) WHERE id=%s''', (Jsonb([event]), job_id))

    def claim(self):
        return self.query('''UPDATE jobs SET status='running', started_at=now(),
            attempts=attempts+1, error=NULL WHERE id=(SELECT id FROM jobs
            WHERE status='queued' AND available_at<=now() ORDER BY created_at
            FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *''', one=True)
