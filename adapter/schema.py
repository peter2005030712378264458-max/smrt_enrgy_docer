"""Explicit, additive upgrade of target aggregates; never connects to source."""
import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from .model import DAILY_METRICS, METRICS, Settings, table_parts
from .runner import RefreshError, target_lock


def upgrade_aggregates(settings: Settings):
    added = []
    with psycopg.connect(settings.target_dsn, autocommit=True, row_factory=dict_row, connect_timeout=8) as connection:
        connection.execute("SET statement_timeout='120s'")
        with target_lock(connection, settings), connection.transaction():
            connection.execute("SET LOCAL lock_timeout='5s'")
            for table, metrics in ((settings.hourly_table, METRICS), (settings.daily_table, DAILY_METRICS)):
                names = table_parts(table)
                columns = connection.execute('''SELECT a.attname, t.typname, c.relkind
                    FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                    JOIN pg_attribute a ON a.attrelid=c.oid JOIN pg_type t ON t.oid=a.atttypid
                    WHERE n.nspname=%s AND c.relname=%s AND a.attnum>0 AND NOT a.attisdropped''', names).fetchall()
                existing = {column['attname']: column for column in columns}
                if (not {'bucket_start', 'sensor_name', 'points_count', *metrics} <= existing.keys()
                        or any(column['relkind'] not in ('r', 'p') for column in columns)):
                    raise RefreshError(f'{table}: expected an existing local aggregate table; no tables were created')
                for metric in metrics:
                    count = metric + '_count'
                    if count in existing:
                        if existing[count]['typname'] != 'int8':
                            raise RefreshError(f'{table}.{count}: expected bigint; upgrade rolled back')
                        continue
                    connection.execute(sql.SQL('ALTER TABLE {} ADD COLUMN {} bigint CHECK ({} >= 0 AND {} <= points_count)').format(
                        sql.Identifier(*names), sql.Identifier(count), sql.Identifier(count), sql.Identifier(count)))
                    added.append(f'{table}.{count}')
    # NULL in legacy rows means unknown counts. Never backfill with points_count:
    # a separate, validated refresh from raw readings supplies exact counts.
    return added
