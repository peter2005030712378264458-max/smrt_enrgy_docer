from psycopg import sql

from .model import METRICS, table_parts


SOURCE_COLUMNS = ("ts", "sensor_name", "pt", "p1", "p2", "p3", "qt", "q1", "q2", "q3",
                  "st", "s1", "s2", "s3", "i1", "i2", "i3", "u1", "u2", "u3", "frequency", "t")


def table(value):
    return sql.Identifier(*table_parts(value))


def phase_total(total, phases):
    return (f"CASE WHEN {total} IS NOT NULL THEN {total} "
            f"WHEN {' OR '.join(p + ' IS NOT NULL' for p in phases)} "
            f"THEN {' + '.join('COALESCE(' + p + ', 0)' for p in phases)} ELSE NULL END")


def phase_average(phases):
    summed = " + ".join(f"COALESCE({p}, 0)" for p in phases)
    count = " + ".join(f"CASE WHEN {p} IS NULL THEN 0 ELSE 1 END" for p in phases)
    return f"({summed})::numeric / NULLIF(({count}), 0)"


EXPRESSIONS = dict(zip(METRICS, (
    phase_total("pt", ("p1", "p2", "p3")), "p1", "p2", "p3",
    phase_total("qt", ("q1", "q2", "q3")), phase_total("st", ("s1", "s2", "s3")),
    phase_average(("i1", "i2", "i3")), phase_average(("u1", "u2", "u3")), "frequency", "t",
)))


def hourly_query(source_table, normalized_source=None):
    # Normalize source values to numeric before transporting aggregate statistics.
    normalized = sql.SQL(", ").join(sql.SQL("({})::numeric AS {}").format(sql.SQL(expression), sql.Identifier(metric))
                                    for metric, expression in EXPRESSIONS.items())
    measures = []
    for metric in METRICS:
        identifier = sql.Identifier(metric)
        # Non-finite numeric values are invalid readings, not useful contributions to AVG.
        valid = sql.SQL("{}::text NOT IN ('NaN', 'Infinity', '-Infinity')").format(identifier)
        measures.extend((
            sql.SQL("SUM({}) FILTER (WHERE {}) AS {}").format(identifier, valid, sql.Identifier(metric + "_sum")),
            sql.SQL("COUNT({}) FILTER (WHERE {}) AS {}").format(identifier, valid, sql.Identifier(metric + "_count")),
        ))
    return sql.SQL("""
        WITH source_rows AS ({source_rows}), normalized AS (
            SELECT ts, sensor_name::text AS sensor_name, {normalized}
            FROM source_rows
            WHERE ts >= %(start)s AND ts < %(end)s AND sensor_name IS NOT NULL
        )
        SELECT date_trunc('hour', ts, %(timezone)s) AS bucket_start, sensor_name,
               COUNT(*) AS points_count,
               MAX(active_power_w_avg) FILTER (
                   WHERE active_power_w_avg::text NOT IN ('NaN', 'Infinity', '-Infinity')
               ) AS active_power_w_max, {measures}
        FROM normalized
        GROUP BY date_trunc('hour', ts, %(timezone)s), sensor_name
        ORDER BY bucket_start, sensor_name
    """).format(normalized=normalized,
                 source_rows=normalized_source or sql.SQL('SELECT * FROM {}').format(table(source_table)),
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
