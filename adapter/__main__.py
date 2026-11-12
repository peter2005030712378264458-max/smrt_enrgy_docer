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
    if argv and argv[0] in ('serve', 'init-control', 'upgrade-control', 'upgrade-aggregates'):
        try:
            if len(argv) != 1:
                raise ValueError('Эта команда не принимает дополнительных аргументов')
            if argv[0] in ('init-control', 'upgrade-control'):
                from .store import Store
                store = Store(os.environ.get('ENERGY_ADAPTER_CONTROL_DSN') or os.environ['ENERGY_AGG_TARGET_DSN'])
                store.install()
                print(f'Служебная схема {store.schema} обновлена до версии 2; агрегаты не изменялись')
            elif argv[0] == 'upgrade-aggregates':
                from .schema import upgrade_aggregates
                target = os.environ['ENERGY_AGG_TARGET_DSN']
                added = upgrade_aggregates(Settings('', target))
                print(f'Счётчики измерений в агрегатах: добавлено {len(added)} столбцов. Старые значения не пересчитаны; источник не изменялся.')
            else:
                from .service import serve
                serve()
            return 0
        except RefreshError as exc:
            print(f'Обновление остановлено: {exc}', file=sys.stderr)
            return 1
        except (ValueError, KeyError, OSError, psycopg.Error):
            print('Не удалось запустить адаптер: проверьте переменные окружения, доступ к БД и init-control', file=sys.stderr)
            return 1
    parser = argparse.ArgumentParser(description="Refresh existing hourly/daily energy tables. Default: read-only dry-run.",
                                     epilog='Service commands: init-control; upgrade-control; upgrade-aggregates; serve')
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
    parser.add_argument("--allow-empty", action="store_true", help="Deprecated compatibility flag; empty profiled days write NULL rows, never clear the expected meter list")
    parser.add_argument("--checkpoint", type=Path, default=Path("energy_aggregation/local.checkpoint.json"))
    parser.add_argument('--initialize-checkpoint', action='store_true',
                        help='Explicitly create a new v2 checkpoint after an explicit --from --apply; never overwrite')
    parser.add_argument('--profile', type=Path, help='Approved JSON source profile')
    args = parser.parse_args(argv)
    if args.initialize_checkpoint and (not args.apply or args.start is None or args.checkpoint.exists()):
        parser.error('--initialize-checkpoint requires --apply, --from and a new checkpoint path')
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
        events = refresh(settings, start=args.start, end=args.end, apply=args.apply, checkpoint_path=args.checkpoint,
                allow_empty=args.allow_empty, allowed_sensors=allowed,
                emit=lambda event: print(json.dumps(event, ensure_ascii=False, default=str), flush=True))
        if args.initialize_checkpoint:
            from datetime import timedelta
            from .runner import save_checkpoint, fingerprint
            from .model import midnight
            outcomes = [e for e in events if e['event'] in ('day', 'day_skipped')]
            if not outcomes:
                raise RefreshError('No completed days to initialize checkpoint')
            end_day = max(date.fromisoformat(e['date']) for e in outcomes) + timedelta(days=1)
            skipped = [e['date'] for e in outcomes if e['event'] == 'day_skipped']
            save_checkpoint(args.checkpoint, fingerprint(settings), midnight(end_day, settings.timezone), skipped)
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
