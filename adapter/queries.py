from psycopg import sql

from .model import METRICS, table_parts


SOURCE_COLUMNS = ("ts", "sensor_name", "pt", "p1", "p2", "p3", "qt", "q1", "q2", "q3",
                  "st", "s1", "s2", "s3", "i1", "i2", "i3", "u1", "u2", "u3", "frequency", "t")


def table(value):
    return sql.Identifier(*table_parts(value))


def phase_total(total, phases):
    return (f"CASE WHEN {total} IS NOT NULL THEN {total} "
            f"WHEN {' AND '.join(p + ' IS NOT NULL' for p in phases)} "
            f"THEN {' + '.join(phases)} ELSE NULL END")


def phase_average(phases):
    summed = " + ".join(f"COALESCE({p}, 0)" for p in phases)
    count = " + ".join(f"CASE WHEN {p} IS NULL THEN 0 ELSE 1 END" for p in phases)
    return f"({summed})::numeric / NULLIF(({count}), 0)"


EXPRESSIONS = dict(zip(METRICS, (
    phase_total("pt", ("p1", "p2", "p3")), "p1", "p2", "p3",
    phase_total("qt", ("q1", "q2", "q3")), phase_total("st", ("s1", "s2", "s3")),
    phase_average(("i1", "i2", "i3")), phase_average(("u1", "u2", "u3")), "frequency", "t",
)))


def hourly_query(source_table, normalized_source=None, *, include_quality=False,
                 sample_seconds=None, window_seconds=10):
    # Normalize source values to numeric before transporting aggregate statistics.
    normalized = sql.SQL(", ").join(sql.SQL("({})::numeric AS {}").format(sql.SQL(expression), sql.Identifier(metric))
                                    for metric, expression in EXPRESSIONS.items())
    unique_metrics, window_metrics, measures = [], [], []
    for metric in METRICS:
        identifier = sql.Identifier(metric)
        unique_metrics.append(sql.SQL('AVG({}) AS {}').format(identifier, identifier))
        window_metrics.extend((
            sql.SQL('SUM({}) AS {}').format(identifier, sql.Identifier(metric + '_sum')),
            sql.SQL('COUNT({}) AS {}').format(identifier, sql.Identifier(metric + '_count')),
        ))
        measures.extend((
            sql.SQL('SUM({}) AS {}').format(sql.Identifier(metric + '_sum'), sql.Identifier(metric + '_sum')),
            sql.SQL('SUM({})::bigint AS {}').format(sql.Identifier(metric + '_count'), sql.Identifier(metric + '_count')),
        ))
    # Deduplicate entire normalized readings before computing per-row totals:
    # phases from separate incomplete records must never form a synthetic total.
    # A timestamp then has one vote regardless of retransmissions. Window sums
    # and counts retain the exact weight of each metric for downstream means.
    sample = sql.Literal(sample_seconds or 0)
    return sql.SQL("""
        WITH source_input AS ({source_rows}), source_rows AS (
            SELECT ts, sensor_name, {finite_columns} FROM source_input
        ), distinct_readings AS MATERIALIZED (
            SELECT DISTINCT * FROM source_rows
            WHERE ts >= %(start)s AND ts < %(end)s AND sensor_name IS NOT NULL
        ), normalized AS MATERIALIZED (
            SELECT ts, sensor_name::text AS sensor_name, {normalized}
            FROM distinct_readings
        ), unique_readings AS MATERIALIZED (
            SELECT ts, sensor_name, {unique_metrics} FROM normalized GROUP BY ts,sensor_name
        ), timed AS (
            SELECT *, LEAD(ts) OVER (PARTITION BY sensor_name ORDER BY ts) AS next_ts
            FROM unique_readings
        ), supports AS (
            SELECT *, LEAST(ts + {sample} * interval '1 second',
                COALESCE(next_ts,%(end)s),%(end)s) AS support_end FROM timed
        ), energy AS (
            SELECT hour AS bucket_start,sensor_name,
                SUM(active_power_w_avg * EXTRACT(EPOCH FROM
                    LEAST(support_end,hour+interval '1 hour')-GREATEST(ts,hour))::numeric)
                    AS power_seconds_sum,
                SUM(EXTRACT(EPOCH FROM
                    LEAST(support_end,hour+interval '1 hour')-GREATEST(ts,hour))::numeric)
                    AS power_seconds_count
            FROM supports CROSS JOIN LATERAL generate_series(
                date_trunc('hour',ts,%(timezone)s),
                date_trunc('hour',support_end-interval '1 microsecond',%(timezone)s),
                interval '1 hour') hour
            WHERE active_power_w_avg IS NOT NULL AND support_end>ts
            GROUP BY hour,sensor_name
        ), windows AS (
            SELECT to_timestamp(floor(EXTRACT(EPOCH FROM ts)/{window})*{window}) AS window_start,
                sensor_name, COUNT(*) AS points_count,
                MAX(active_power_w_avg) AS active_power_w_max, {window_metrics}
            FROM unique_readings GROUP BY window_start,sensor_name
        ), hourly AS (
            SELECT date_trunc('hour',window_start,%(timezone)s) AS bucket_start,sensor_name,
                SUM(points_count)::bigint AS points_count,
                MAX(active_power_w_max) AS active_power_w_max,{measures}
            FROM windows GROUP BY bucket_start,sensor_name
        )
        , keys AS (
            SELECT bucket_start,sensor_name FROM hourly
            UNION SELECT bucket_start,sensor_name FROM energy
        )
        SELECT k.bucket_start,k.sensor_name,COALESCE(h.points_count,0)::bigint AS points_count,
            h.active_power_w_max,{joined_metrics},e.power_seconds_sum,e.power_seconds_count,
            COALESCE(h.points_count,0)::bigint AS unique_points_count
        FROM keys k LEFT JOIN hourly h USING(bucket_start,sensor_name)
        LEFT JOIN energy e USING(bucket_start,sensor_name)
        ORDER BY k.bucket_start,k.sensor_name
    """).format(normalized=normalized,
                 finite_columns=sql.SQL(', ').join(
                     sql.SQL("CASE WHEN {}::text IN ('NaN','Infinity','-Infinity') THEN NULL ELSE {} END AS {}").format(
                         sql.Identifier(field), sql.Identifier(field), sql.Identifier(field))
                     for field in SOURCE_COLUMNS if field not in ('ts', 'sensor_name')),
                 source_rows=normalized_source or sql.SQL('SELECT * FROM {}').format(table(source_table)),
                 sample=sample, window=sql.Literal(window_seconds),
                 unique_metrics=sql.SQL(', ').join(unique_metrics),
                 joined_metrics=sql.SQL(', ').join(
                     sql.SQL('h.{sum},COALESCE(h.{count},0)::bigint AS {count}').format(
                         sum=sql.Identifier(metric+'_sum'), count=sql.Identifier(metric+'_count'))
                     for metric in METRICS),
                 window_metrics=sql.SQL(', ').join(window_metrics),
                 measures=sql.SQL(", ").join(measures))


def insert_query(target_table, columns):
    return sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
        table(target_table), sql.SQL(", ").join(map(sql.Identifier, columns)),
        sql.SQL(", ").join(sql.Placeholder() for _ in columns),
    )


def window_query(target_table, exists=False):
    if exists:
        return sql.SQL("SELECT EXISTS(SELECT 1 FROM {} WHERE bucket_start >= %s AND bucket_start < %s)").format(table(target_table))
    return sql.SQL("DELETE FROM {} WHERE bucket_start >= %s AND bucket_start < %s").format(table(target_table))
