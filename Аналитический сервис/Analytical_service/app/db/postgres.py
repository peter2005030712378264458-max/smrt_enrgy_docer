from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row

from app.core.config import Settings, get_settings


def build_connection_kwargs(settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    return {
        "host": settings.postgres_host,
        "port": settings.postgres_port,
        "dbname": settings.postgres_db,
        "user": settings.postgres_user,
        "password": settings.postgres_password,
        "connect_timeout": settings.postgres_connect_timeout,
        "keepalives": settings.postgres_keepalives,
        "keepalives_idle": settings.postgres_keepalives_idle,
        "keepalives_interval": settings.postgres_keepalives_interval,
        "keepalives_count": settings.postgres_keepalives_count,
        "row_factory": dict_row,
    }


@contextmanager
def get_connection(settings: Settings | None = None) -> Iterator[psycopg.Connection]:
    with psycopg.connect(**build_connection_kwargs(settings)) as connection:
        yield connection
