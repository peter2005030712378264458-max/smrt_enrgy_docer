"""Control data only. Aggregate schemas are never migrated by the service."""
from contextlib import contextmanager
import uuid

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
'''


class Store:
    def __init__(self, dsn):
        self.dsn = dsn

    @contextmanager
    def connection(self):
        with psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=8) as conn:
            conn.execute("SET statement_timeout='15s'")
            yield conn

    def query(self, text, params=(), one=False):
        with self.connection() as conn:
            cursor = conn.execute(text, params)
            if cursor.description:
                return cursor.fetchone() if one else cursor.fetchall()

    def install(self):
        with self.connection() as conn:
            conn.execute(DDL)

    def profile(self, profile_id):
        row = self.query('SELECT * FROM adapter_control.profiles WHERE id=%s', (profile_id,), one=True)
        if not row:
            raise ValueError('Профиль не найден')
        return row

    def job(self, job_id):
        row = self.query('SELECT * FROM adapter_control.jobs WHERE id=%s', (job_id,), one=True)
        if not row:
            raise ValueError('Задание не найдено')
        return row

    def enqueue(self, profile_id, kind, begin=None, end=None, preview_id=None, slot=None):
        job_id = str(uuid.uuid4())
        row = self.query('''INSERT INTO adapter_control.jobs(id,profile_id,kind,date_from,date_to,preview_id,slot)
            VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(slot) DO NOTHING RETURNING *''',
            (job_id, profile_id, kind, begin, end, preview_id, slot), one=True)
        return row

    def event(self, job_id, event):
        self.query('''UPDATE adapter_control.jobs SET events=(
            SELECT COALESCE(jsonb_agg(value ORDER BY n),'[]'::jsonb) FROM
            (SELECT value,n FROM jsonb_array_elements(events || %s::jsonb) WITH ORDINALITY AS e(value,n)
             ORDER BY n DESC LIMIT 800) recent) WHERE id=%s''', (Jsonb([event]), job_id))

    def claim(self):
        return self.query('''UPDATE adapter_control.jobs SET status='running', started_at=now(),
            attempts=attempts+1, error=NULL WHERE id=(SELECT id FROM adapter_control.jobs
            WHERE status='queued' AND available_at<=now() ORDER BY created_at
            FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *''', one=True)
