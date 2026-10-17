import argparse
from datetime import date
from decimal import Decimal
import json
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfoNotFoundError

import psycopg

from .model import Settings
from .runner import RefreshError, refresh


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ('serve', 'init-control'):
        try:
            if len(argv) != 1:
                raise ValueError('Эта команда не принимает дополнительных аргументов')
            if argv[0] == 'init-control':
                from .store import Store
                Store(os.environ.get('ENERGY_ADAPTER_CONTROL_DSN') or os.environ['ENERGY_AGG_TARGET_DSN']).install()
                print('Служебная схема adapter_control установлена; агрегаты не изменялись')
            else:
                from .service import serve
                serve()
            return 0
        except (ValueError, KeyError, OSError, psycopg.Error):
            print('Не удалось запустить адаптер: проверьте переменные окружения, доступ к БД и init-control', file=sys.stderr)
            return 1
    parser = argparse.ArgumentParser(description="Refresh existing hourly/daily energy tables. Default: read-only dry-run.",
                                     epilog='Service commands: python -m adapter init-control; python -m adapter serve')
    parser.add_argument("--from", dest="start", type=date.fromisoformat, help="First local date, inclusive")
    parser.add_argument("--to", dest="end", type=date.fromisoformat, help="Boundary date, exclusive; unfinished days are always excluded")
    parser.add_argument("--apply", action="store_true", help="Replace aggregate rows in the selected period; never writes to source")
    parser.add_argument("--lookback-days", type=int, default=2)
    parser.add_argument("--timezone", default="Europe/Moscow")
    parser.add_argument("--statement-timeout", type=int, default=120, help="Seconds per SQL statement")
    parser.add_argument("--source-table", default="public.electricity_sensor_readings")
    parser.add_argument("--hourly-table", default="student_schema.electricity_sensor_readings_hourly")
    parser.add_argument("--daily-table", default="student_schema.electricity_sensor_readings_daily")
    energy = parser.add_mutually_exclusive_group()
    energy.add_argument("--sample-seconds", type=Decimal, help="Confirmed uniform measurement interval; energy estimate assumes watts")
    energy.add_argument("--no-energy", action="store_true", help="Explicitly store NULL energy until sampling/integration is defined")
    parser.add_argument("--allow-empty", action="store_true", help="Allow empty source days to clear existing target aggregates")
    parser.add_argument("--checkpoint", type=Path, default=Path("energy_aggregation/local.checkpoint.json"))
    parser.add_argument('--profile', type=Path, help='Approved JSON source profile')
    args = parser.parse_args(argv)
    if args.apply and args.sample_seconds is None and not args.no_energy and not args.profile:
        parser.error("--apply requires --sample-seconds or --no-energy; the interval is never guessed")
    source_dsn = os.environ.get("ENERGY_AGG_SOURCE_DSN")
    target_dsn = os.environ.get("ENERGY_AGG_TARGET_DSN")
    if not target_dsn or (not source_dsn and not args.profile):
        parser.error("Set ENERGY_AGG_SOURCE_DSN and ENERGY_AGG_TARGET_DSN; credentials are not command-line arguments")
    try:
        profile = None
        allowed = None
        if args.profile:
            from .mapping import validate_profile
            from .service import directory_names, connections
            profile = validate_profile(json.loads(args.profile.read_text(encoding='utf-8')))
            registry = connections()
            if profile['connection'] not in registry:
                raise ValueError('Неизвестное подключение профиля')
            source_dsn = registry[profile['connection']]
            allowed = directory_names(profile, registry)
            if args.no_energy or (args.sample_seconds is not None and args.sample_seconds != Decimal(str(profile['sample_seconds']))):
                raise ValueError('Параметры энергии задаются утверждённым профилем')
            args.source_table = profile['source_table']
            args.timezone = profile.get('timezone', 'Europe/Moscow')
            args.sample_seconds = Decimal(str(profile['sample_seconds']))
        settings = Settings(source_dsn, target_dsn, source_table=args.source_table,
                            hourly_table=args.hourly_table, daily_table=args.daily_table,
                            timezone=args.timezone, lookback_days=args.lookback_days,
                            statement_timeout_seconds=args.statement_timeout, sample_seconds=args.sample_seconds, profile=profile)
        refresh(settings, start=args.start, end=args.end, apply=args.apply, checkpoint_path=args.checkpoint,
                allow_empty=args.allow_empty, allowed_sensors=allowed,
                emit=lambda event: print(json.dumps(event, ensure_ascii=False, default=str), flush=True))
    except (RefreshError, ValueError, OSError, ZoneInfoNotFoundError) as exc:
        print(f"Refresh stopped: {exc}", file=sys.stderr)
        return 1
    except psycopg.Error as exc:
        # Avoid printing a DSN or raw exception with connection settings.
        print(f"Database operation failed (SQLSTATE {exc.sqlstate or 'unavailable'}). "
              "The current day was not checkpointed; already committed days can be retried.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
