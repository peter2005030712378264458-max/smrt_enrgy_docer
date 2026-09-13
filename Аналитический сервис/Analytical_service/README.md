# Analytical Service

FastAPI-сервис для аналитических расчетов энергопотребления. Первый сценарий сервиса сравнивает два непересекающихся периода и выполняет z-тест гипотезы о равенстве средних:

```text
H0: среднее потребление в двух периодах одинаковое
```

В статистической записи:

```text
H0: μ1 = μ2
```

Альтернативная гипотеза выбирается параметром `alternative`:

- `two_sided`: `H1: μ1 ≠ μ2`, средние различаются;
- `greater`: `H1: μ1 > μ2`, среднее первого периода больше второго;
- `less`: `H1: μ1 < μ2`, среднее первого периода меньше второго.

Критическое значение берется из таблицы z-распределения по правилу:

```text
two_sided:
  target_cdf = 1 - alpha / 2
  reject_null = abs(z_statistic) > z_critical

greater:
  target_cdf = 1 - alpha
  reject_null = z_statistic > z_critical

less:
  target_cdf = 1 - alpha
  reject_null = z_statistic < -z_critical
```

Например, при `alpha = 0.05` для двухстороннего теста используется табличное значение около `1.96`, а для односторонних гипотез - около `1.65`.

Frontend обращается не к этому сервису напрямую, а к Django API Gateway. Django проверяет JWT пользователя, применяет фильтры счетчиков и вызывает внутренний endpoint FastAPI.

## Локальная установка

```bash
cd "/Users/petrtarancenko/ВУЗ/4 семестр/Умное энергопотребление/Аналитический сервис/Analytical_service"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Настройки

Пример переменных окружения лежит в `.env.example`.

Основные переменные:

```text
ANALYTICS_INTERNAL_TOKEN=change-me
ANALYTICS_POSTGRES_HOST=127.0.0.1
ANALYTICS_POSTGRES_PORT=15432
ANALYTICS_POSTGRES_DB=student
ANALYTICS_POSTGRES_USER=student
ANALYTICS_POSTGRES_PASSWORD=st1211@98w
ANALYTICS_HOURLY_TABLE=student_schema.electricity_sensor_readings_hourly
ANALYTICS_TIMEZONE=Europe/Moscow
```

Для production `ANALYTICS_INTERNAL_TOKEN` должен быть задан. Если токен не задан, проверка `X-Internal-Token` отключена для локальной разработки.

## Запуск

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload
```

Проверка:

```bash
curl http://127.0.0.1:8001/health
```

Пример внутреннего запроса:

```bash
curl -X POST http://127.0.0.1:8001/internal/analytics/period-comparison \
  -H "Content-Type: application/json" \
  -H "X-Internal-Token: change-me" \
  -d '{
    "period_1": {"date_from": "2021-01-03", "date_to": "2021-01-10"},
    "period_2": {"date_from": "2021-02-01", "date_to": "2021-02-07"},
    "alpha": 0.05,
    "alternative": "greater",
    "data_names": null,
    "metric": "active_power_w_avg"
  }'
```

## Z-таблица

Критическое значение z берется из `app/data/standard_normal_cdf.csv`. Таблица соответствует стандартной нормальной таблице с шагом `0.01`.

Источник для сверки: NIST Engineering Statistics Handbook, страница `Cumulative Distribution Function of the Standard Normal Distribution`: `https://www.itl.nist.gov/div898/handbook/eda/section3/eda3671.htm`.

## Тесты

```bash
pytest
```
