"""Initialize a separate local control schema in the existing database."""
import os

from psycopg.types.json import Jsonb

from .service import build_settings, connections
from .runner import fingerprint
from .store import Store


def main():
    store = Store(os.environ.get('ENERGY_ADAPTER_CONTROL_DSN') or os.environ['ENERGY_AGG_TARGET_DSN'])
    if store.schema == 'adapter_control':
        raise ValueError('Для локального запуска задайте отдельную служебную схему')
    store.install()
    registry = connections()
    copied = 0
    with store.connection() as conn:
        rows = conn.execute("SELECT id,document FROM adapter_control.profiles WHERE document ? 'columns'").fetchall()
        for row in rows:
            if row['document'].get('connection') not in registry:
                continue
            try:
                signature = fingerprint(build_settings(row['document'], registry))
            except (ValueError, KeyError):
                continue
            cursor = conn.execute('INSERT INTO profiles(id,document,signature) VALUES(%s,%s,%s) ON CONFLICT(id) DO NOTHING',
                                  (row['id'], Jsonb(row['document']), signature))
            copied += cursor.rowcount
    print(f'Local control schema ready; imported {copied} profiles as inactive and unvalidated. No aggregates changed.')


if __name__ == '__main__':
    main()
