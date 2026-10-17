-- Почасовая таблица 
CREATE TABLE IF NOT EXISTS electricity_sensor_readings_hourly (
    bucket_start timestamptz NOT NULL,
    sensor_name text NOT NULL,

    points_count bigint NOT NULL,

    active_power_w_avg numeric,
    active_power_w_max numeric,

    phase1_power_w_avg numeric,
    phase2_power_w_avg numeric,
    phase3_power_w_avg numeric,

    reactive_power_var_avg numeric,
    apparent_power_va_avg numeric,

    current_avg_a numeric,
    voltage_avg_v numeric,
    frequency_hz_avg numeric,
    meter_temperature_avg numeric,

    energy_kwh numeric,

    PRIMARY KEY (bucket_start, sensor_name)
);

-- Заполнение почасовой таблицы 
INSERT INTO electricity_sensor_readings_hourly
SELECT
    date_trunc('hour', ts) AS bucket_start,
    sensor_name::text,

    COUNT(*) AS points_count,

    AVG(COALESCE(pt, COALESCE(p1, 0) + COALESCE(p2, 0) + COALESCE(p3, 0))) AS active_power_w_avg,
    MAX(COALESCE(pt, COALESCE(p1, 0) + COALESCE(p2, 0) + COALESCE(p3, 0))) AS active_power_w_max,

    AVG(p1),
    AVG(p2),
    AVG(p3),

    AVG(COALESCE(qt, COALESCE(q1, 0) + COALESCE(q2, 0) + COALESCE(q3, 0))),
    AVG(COALESCE(st, COALESCE(s1, 0) + COALESCE(s2, 0) + COALESCE(s3, 0))),

    AVG((COALESCE(i1, 0) + COALESCE(i2, 0) + COALESCE(i3, 0)) /
        NULLIF(
            (CASE WHEN i1 IS NULL THEN 0 ELSE 1 END) +
            (CASE WHEN i2 IS NULL THEN 0 ELSE 1 END) +
            (CASE WHEN i3 IS NULL THEN 0 ELSE 1 END),
            0
        )),

    AVG((COALESCE(u1, 0) + COALESCE(u2, 0) + COALESCE(u3, 0)) /
        NULLIF(
            (CASE WHEN u1 IS NULL THEN 0 ELSE 1 END) +
            (CASE WHEN u2 IS NULL THEN 0 ELSE 1 END) +
            (CASE WHEN u3 IS NULL THEN 0 ELSE 1 END),
            0
        )),

    AVG(frequency),
    AVG(t),

    SUM(COALESCE(pt, COALESCE(p1, 0) + COALESCE(p2, 0) + COALESCE(p3, 0)) / 60000.0) AS energy_kwh

FROM electricity_sensor_readings
WHERE sensor_name IS NOT NULL
GROUP BY date_trunc('hour', ts), sensor_name
ON CONFLICT (bucket_start, sensor_name) DO UPDATE SET
    points_count = EXCLUDED.points_count,
    active_power_w_avg = EXCLUDED.active_power_w_avg,
    active_power_w_max = EXCLUDED.active_power_w_max,
    energy_kwh = EXCLUDED.energy_kwh;

-- Посуточная таблица
CREATE TABLE IF NOT EXISTS electricity_sensor_readings_daily (
    bucket_start date NOT NULL,
    sensor_name text NOT NULL,

    points_count bigint NOT NULL,

    active_power_w_avg numeric,
    active_power_w_max numeric,

    reactive_power_var_avg numeric,
    apparent_power_va_avg numeric,

    voltage_avg_v numeric,
    frequency_hz_avg numeric,
    meter_temperature_avg numeric,

    energy_kwh numeric,

    PRIMARY KEY (bucket_start, sensor_name)
);

-- Заполнение посуточной таблицы 
INSERT INTO electricity_sensor_readings_daily
SELECT
    bucket_start::date AS bucket_start,
    sensor_name,

    SUM(points_count) AS points_count,

    AVG(active_power_w_avg) AS active_power_w_avg,
    MAX(active_power_w_max) AS active_power_w_max,

    AVG(reactive_power_var_avg) AS reactive_power_var_avg,
    AVG(apparent_power_va_avg) AS apparent_power_va_avg,

    AVG(voltage_avg_v) AS voltage_avg_v,
    AVG(frequency_hz_avg) AS frequency_hz_avg,
    AVG(meter_temperature_avg) AS meter_temperature_avg,

    SUM(energy_kwh) AS energy_kwh

FROM electricity_sensor_readings_hourly
GROUP BY bucket_start::date, sensor_name
ON CONFLICT (bucket_start, sensor_name) DO UPDATE SET
    points_count = EXCLUDED.points_count,
    active_power_w_avg = EXCLUDED.active_power_w_avg,
    active_power_w_max = EXCLUDED.active_power_w_max,
    energy_kwh = EXCLUDED.energy_kwh;

-- Индексы 
CREATE INDEX IF NOT EXISTS electricity_sensor_readings_daily_sensor_time_idx
ON electricity_sensor_readings_daily (sensor_name, bucket_start);

CREATE INDEX IF NOT EXISTS electricity_sensor_readings_daily_time_idx
ON electricity_sensor_readings_daily (bucket_start);
