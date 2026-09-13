# План реализации аналитического сервиса сравнения периодов

## 0. Контекст и ограничения

Целевая ветка для всех трех рабочих деревьев: `analis_serv`.

Перед реализацией агент должен проверить текущую ветку отдельно в:

- `/Users/petrtarancenko/ВУЗ/4 семестр/Умное энергопотребление/Аналитический сервис/Analytical_service`
- `/Users/petrtarancenko/ВУЗ/4 семестр/Умное энергопотребление/Бэк/project_backend`
- `/Users/petrtarancenko/ВУЗ/4 семестр/Умное энергопотребление/Фронт/smart_energy`

Команда проверки:

```bash
git branch --show-current
```

Если хотя бы в одной папке ветка не `analis_serv`, остановиться и запросить инструкцию у пользователя. Не выполнять `git checkout`, `git pull`, `git push`, `git reset` и другие команды, меняющие состояние Git.

Сейчас архитектурные ADR в бэке уже описывают нужный подход:

- Django REST Framework остается API Gateway и точкой входа для React.
- Аналитика выносится в отдельный FastAPI-сервис.
- Frontend не должен обращаться в FastAPI напрямую, чтобы не дублировать авторизацию и CORS-логику.

Итоговая схема:

```text
React page "Аналитика"
  -> /api/consumption/analytics/period-comparison/  (Django, JWT)
  -> /internal/analytics/period-comparison           (FastAPI, internal token)
  -> PostgreSQL student_schema.electricity_sensor_readings_hourly
```

## 1. Что должно получиться

На отдельной странице `Аналитика` пользователь выбирает два непересекающихся периода:

- дата начала и дата конца первого периода;
- дата начала и дата конца второго периода;
- уровень значимости `alpha`, по умолчанию `0.05`;
- опционально тот же контекст потребления, что на дашборде: все счетчики, конкретный счетчик, помещение или класс потребителя.

После нажатия `Сравнить` выводятся:

- среднее потребление за первый период;
- среднее потребление за второй период;
- количество наблюдений в каждом периоде;
- суммарная энергия в каждом периоде;
- `z` статистическое;
- `z` критическое;
- уровень значимости;
- итог: отвергаем или не отвергаем нулевую гипотезу.

Статистическая постановка:

- `H0`: среднее потребление в двух периодах одинаковое, то есть `mu1 = mu2`.
- `H1`: средние различаются, то есть `mu1 != mu2`.
- Тест двухсторонний.
- Предположение: распределение нормально.
- Наблюдение: один часовой bucket суммарной активной мощности по выбранным счетчикам.
- Метрика сравнения: средняя активная мощность, кВт.

Формулы:

```text
mean_1 = AVG(hourly_total_kw for period_1)
mean_2 = AVG(hourly_total_kw for period_2)

s1 = sample standard deviation of hourly_total_kw for period_1
s2 = sample standard deviation of hourly_total_kw for period_2

standard_error = sqrt((s1^2 / n1) + (s2^2 / n2))
z_statistic = (mean_1 - mean_2) / standard_error

target_cdf = 1 - alpha / 2
z_critical = lookup in z-table by target_cdf

reject_null = abs(z_statistic) > z_critical
```

Для `alpha = 0.05` ожидаемое табличное критическое значение: около `1.96`.

## 2. Таблица z-распределения

Требование пользователя: критическое значение брать именно из таблицы z-распределения, сохраненной в проекте.

Использовать источник NIST Engineering Statistics Handbook:

- URL: `https://www.itl.nist.gov/div898/handbook/eda/section3/eda3671.htm`
- Название страницы: `Cumulative Distribution Function of the Standard Normal Distribution`
- Таблица NIST содержит площадь под стандартной нормальной кривой от `0` до `z`.
- На той же странице приведены распространенные критические значения, включая `+1.960` для cumulative probability `0.975`.

Резервный академический источник для проверки значений:

- URL: `https://online.stat.psu.edu/stat500/Z_table.pdf`
- Название: `Standard Normal Cumulative Probability Table`
- Таблица Penn State содержит cumulative probabilities `P(Z <= z)`.

Рекомендуемый формат внутри FastAPI-сервиса:

```text
app/data/standard_normal_cdf.csv
```

CSV должен быть переписан из таблицы, а не вычисляться на лету. Формат:

```csv
z,area_0_to_z,cdf
0.00,0.00000,0.50000
0.01,0.00399,0.50399
0.02,0.00798,0.50798
...
1.96,0.47500,0.97500
...
3.89,0.49995,0.99995
```

Пояснение:

- `area_0_to_z` берется из таблицы NIST.
- `cdf` можно записать в CSV как `0.5 + area_0_to_z`, чтобы lookup был простым и без вычисления CDF.
- Достаточно положительных `z`, потому что для двухстороннего критерия используется положительное `z_(1 - alpha/2)`.
- Диапазон минимум `0.00..3.89` с шагом `0.01`; это покрывает обычные уровни значимости `0.10`, `0.05`, `0.01`, `0.001`.

Правило lookup:

1. Проверить, что `0 < alpha < 1`.
2. Посчитать `target_cdf = 1 - alpha / 2`.
3. Найти первую строку CSV, где `cdf >= target_cdf`.
4. Вернуть `z` этой строки.
5. Если `target_cdf` больше максимального `cdf` в таблице, вернуть ошибку валидации: уровень значимости слишком мал для доступной таблицы.

Такой lookup чуть консервативен из-за округления таблицы до `0.01`, но он полностью соответствует требованию брать критическое значение из сохраненной таблицы.

## 3. Аналитический сервис FastAPI

Рабочая папка:

```text
/Users/petrtarancenko/ВУЗ/4 семестр/Умное энергопотребление/Аналитический сервис/Analytical_service
```

Создать структуру:

```text
Analytical_service/
  README.md
  requirements.txt
  .env.example
  app/
    __init__.py
    main.py
    api/
      __init__.py
      routes.py
    core/
      __init__.py
      config.py
      security.py
    db/
      __init__.py
      postgres.py
    schemas/
      __init__.py
      period_comparison.py
    services/
      __init__.py
      period_comparison.py
      z_table.py
    data/
      standard_normal_cdf.csv
  tests/
    test_period_comparison.py
    test_z_table.py
```

### 3.1. requirements.txt

Минимальные зависимости:

```text
fastapi
uvicorn[standard]
pydantic-settings
psycopg[binary,pool]==3.2.9
pytest
httpx
```

Не добавлять `numpy`, `pandas`, `scipy` для этой задачи. Расчеты простые, а критическое значение должно браться из CSV-таблицы.

### 3.2. Конфигурация

Файл `app/core/config.py`.

Использовать `pydantic-settings`.

Поля:

```python
service_name: str = "analytical-service"
internal_token: str | None = None
postgres_host: str = "127.0.0.1"
postgres_port: int = 15432
postgres_db: str = "student"
postgres_user: str = "student"
postgres_password: str = "st1211@98w"
postgres_connect_timeout: int = 5
postgres_keepalives: int = 1
postgres_keepalives_idle: int = 30
postgres_keepalives_interval: int = 10
postgres_keepalives_count: int = 5
hourly_table: str = "student_schema.electricity_sensor_readings_hourly"
timezone_name: str = "Europe/Moscow"
```

Поддержать env names:

```text
ANALYTICS_INTERNAL_TOKEN
ANALYTICS_POSTGRES_HOST
ANALYTICS_POSTGRES_PORT
ANALYTICS_POSTGRES_DB
ANALYTICS_POSTGRES_USER
ANALYTICS_POSTGRES_PASSWORD
ANALYTICS_POSTGRES_CONNECT_TIMEOUT
ANALYTICS_HOURLY_TABLE
ANALYTICS_TIMEZONE
```

Для удобства локальной интеграции можно также читать fallback-переменные, уже используемые Django:

```text
ENERGY_AGG_POSTGRES_HOST
ENERGY_AGG_POSTGRES_PORT
ENERGY_AGG_POSTGRES_DB
ENERGY_AGG_POSTGRES_USER
ENERGY_AGG_POSTGRES_PASSWORD
ENERGY_HOURLY_TABLE
POSTGRES_HOST
POSTGRES_PORT
```

Важно: имя таблицы подставляется в SQL как identifier, поэтому перед использованием валидировать regex:

```text
^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$
```

### 3.3. Безопасность internal endpoint

Файл `app/core/security.py`.

FastAPI endpoint должен принимать заголовок:

```text
X-Internal-Token: <token>
```

Если `ANALYTICS_INTERNAL_TOKEN` задан, сервис сравнивает заголовок с ним через `hmac.compare_digest`.

Если токен не задан, проверку пропускать. Это удобно для локальной разработки, но в `.env.example` явно указать, что для production token обязателен.

### 3.4. Pydantic-схемы

Файл `app/schemas/period_comparison.py`.

Request:

```python
class PeriodInput(BaseModel):
    date_from: date
    date_to: date

class PeriodComparisonRequest(BaseModel):
    period_1: PeriodInput
    period_2: PeriodInput
    alpha: float = 0.05
    data_names: list[str] | None = None
    metric: Literal["active_power_w_avg"] = "active_power_w_avg"
```

Валидация:

- `period.date_from <= period.date_to`;
- периоды не пересекаются:
  - корректно, если `period_1.date_to < period_2.date_from`;
  - корректно, если `period_2.date_to < period_1.date_from`;
  - иначе ошибка `422`;
- `alpha` в диапазоне `0 < alpha < 1`;
- если `data_names` передан, убрать пустые строки и дубликаты.

Response:

```python
class PeriodStats(BaseModel):
    key: Literal["period_1", "period_2"]
    date_from: date
    date_to: date
    actual_from: datetime | None
    actual_to: datetime | None
    observations: int
    source_points: int
    mean_kw: float | None
    stddev_kw: float | None
    variance_kw2: float | None
    total_energy_kwh: float

class ZTableLookup(BaseModel):
    source: str
    target_cdf: float
    matched_z: float
    matched_cdf: float
    alpha: float

class PeriodComparisonResponse(BaseModel):
    hypothesis: str
    alternative: Literal["two_sided"]
    alpha: float
    metric: Literal["active_power_w_avg"]
    unit: Literal["kW"]
    periods: list[PeriodStats]
    difference_mean_kw: float | None
    standard_error: float | None
    z_statistic: float | None
    z_critical: float
    reject_null: bool | None
    conclusion: str
    table_lookup: ZTableLookup
```

### 3.5. SQL-запрос для одного периода

Файл `app/services/period_comparison.py`.

Периоды приходят как даты в московской зоне. Преобразование:

```text
date_from -> YYYY-MM-DDT00:00:00+03:00
date_to   -> следующий день после date_to, YYYY-MM-DDT00:00:00+03:00
```

В SQL использовать полуоткрытый интервал:

```sql
bucket_start >= %(from_ts)s::timestamptz
AND bucket_start < %(to_ts_exclusive)s::timestamptz
```

Это лучше, чем `23:59:59`, потому что не теряет значения с дробными секундами.

Запрос:

```sql
WITH hourly AS (
    SELECT
        a.bucket_start,
        SUM(a.active_power_w_avg) / 1000.0 AS hourly_total_kw,
        SUM(a.active_power_w_avg) / 1000.0 AS hourly_energy_kwh,
        SUM(a.points_count) AS source_points
    FROM {hourly_table} a
    WHERE a.bucket_start >= %(from_ts)s::timestamptz
      AND a.bucket_start < %(to_ts_exclusive)s::timestamptz
      -- добавить только если data_names не null:
      AND a.sensor_name::text = ANY(%(data_names)s)
    GROUP BY a.bucket_start
)
SELECT
    COUNT(*) AS observations,
    COALESCE(SUM(source_points), 0) AS source_points,
    AVG(hourly_total_kw) AS mean_kw,
    STDDEV_SAMP(hourly_total_kw) AS stddev_kw,
    VAR_SAMP(hourly_total_kw) AS variance_kw2,
    COALESCE(SUM(hourly_energy_kwh), 0) AS total_energy_kwh,
    MIN(bucket_start) AS actual_from,
    MAX(bucket_start) AS actual_to
FROM hourly;
```

Почему `SUM(active_power_w_avg) / 1000.0`:

- текущий дашборд уже использует такую агрегацию для суммарной мощности по счетчикам;
- один `bucket_start` соответствует часу;
- средняя мощность за час в ваттах, деленная на `1000`, дает кВт;
- для часового bucket то же значение численно соответствует кВт·ч за этот час.

### 3.6. Расчет z-статистики

После получения двух `PeriodStats`:

1. Если в одном из периодов `observations < 2`, вернуть `422` с сообщением: `Для z-теста нужно минимум 2 часовых наблюдения в каждом периоде`.
2. Если `stddev_kw` равен `null`, считать это недостатком данных.
3. Посчитать `standard_error`.
4. Если `standard_error == 0`:
   - если `mean_kw` равны, вернуть `z_statistic = 0`, `reject_null = False`, conclusion о том, что различий не обнаружено при нулевой дисперсии;
   - если средние различаются, вернуть `422`, потому что формальная z-статистика не определена при нулевой стандартной ошибке.
5. Иначе посчитать `z_statistic`.
6. Получить `z_critical` через `ZTable.lookup_critical(alpha)`.
7. `reject_null = abs(z_statistic) > z_critical`.

Текст conclusion:

- если `reject_null = True`: `Нулевая гипотеза отвергается: среднее потребление в выбранных периодах статистически значимо различается при alpha = 0.05.`
- если `reject_null = False`: `Нет оснований отвергнуть нулевую гипотезу: статистически значимого различия среднего потребления при alpha = 0.05 не обнаружено.`

Подставлять фактический `alpha`.

### 3.7. FastAPI routes

Файл `app/api/routes.py`.

Endpoints:

```text
GET  /health
POST /internal/analytics/period-comparison
```

`GET /health` возвращает:

```json
{"status":"ok","service":"analytical-service"}
```

`POST /internal/analytics/period-comparison`:

- проверяет `X-Internal-Token`;
- валидирует request;
- вызывает сервис расчета;
- возвращает `PeriodComparisonResponse`.

Файл `app/main.py`:

```python
from fastapi import FastAPI
from app.api.routes import router

app = FastAPI(title="Smart Energy Analytical Service")
app.include_router(router)
```

Локальный запуск:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload
```

### 3.8. README аналитического сервиса

Заполнить `README.md`:

- назначение сервиса;
- локальная установка;
- переменные окружения;
- пример запуска;
- пример запроса `curl`;
- описание источника z-таблицы;
- как запускать тесты.

## 4. Изменения в Django backend

Рабочая папка:

```text
/Users/petrtarancenko/ВУЗ/4 семестр/Умное энергопотребление/Бэк/project_backend
```

### 4.1. requirements.txt

Добавить:

```text
httpx
```

### 4.2. settings.py

Добавить настройки:

```python
ANALYTICS_SERVICE_URL = os.getenv("ANALYTICS_SERVICE_URL", "http://127.0.0.1:8001")
ANALYTICS_SERVICE_TIMEOUT_SECONDS = float(os.getenv("ANALYTICS_SERVICE_TIMEOUT_SECONDS", "10"))
ANALYTICS_SERVICE_TOKEN = os.getenv("ANALYTICS_SERVICE_TOKEN", "")
```

### 4.3. Gateway client

Создать файл:

```text
apps/consumption/analytics_client.py
```

Функция:

```python
def request_period_comparison(payload: dict) -> dict:
    ...
```

Логика:

- endpoint: `{settings.ANALYTICS_SERVICE_URL.rstrip("/")}/internal/analytics/period-comparison`;
- метод `POST`;
- JSON body из payload;
- timeout из settings;
- если `ANALYTICS_SERVICE_TOKEN` не пустой, добавить `X-Internal-Token`;
- при `httpx.TimeoutException` вернуть DRF-friendly exception;
- при `httpx.RequestError` вернуть сообщение `Аналитический сервис недоступен`;
- при `4xx/5xx` пробросить `detail` из FastAPI, но не отдавать пользователю stack trace.

Можно использовать обычный sync `httpx.Client`, потому что DRF views синхронные.

### 4.4. DRF view

Создать файл:

```text
apps/consumption/analytics_views.py
```

Класс:

```python
class PeriodComparisonView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        ...
```

Почему `GET`: frontend запрашивает расчет по параметрам, состояние не меняется. Django внутри может делать `POST` в FastAPI, это внутренний контракт.

Query params:

```text
period1_from=YYYY-MM-DD
period1_to=YYYY-MM-DD
period2_from=YYYY-MM-DD
period2_to=YYYY-MM-DD
alpha=0.05
data_name=<optional>
room=<optional>
consumer_class=<optional>
building=<optional>
floor=<optional>
```

Валидация в Django:

- проверить обязательные даты;
- формат строго `YYYY-MM-DD`;
- `date_from <= date_to`;
- периоды не пересекаются;
- `alpha` число, `0 < alpha < 1`;
- если ошибка, вернуть `400` с `{"detail": "..."}`.

Фильтры:

- использовать существующую функцию `dashboard_queries._matching_data_names(connection, request)`.
- Делать это через `dashboard_connection(dashboard_queries._metadata_db_alias())`, как в текущем `get_summary`.
- Если функция вернула `[]`, вернуть `400` с `{"detail":"По выбранным фильтрам не найдено счетчиков"}`.
- Если функция вернула `None`, передать в FastAPI `data_names: null`, то есть все счетчики.
- Если вернула список, передать его.

Payload в FastAPI:

```python
{
    "period_1": {
        "date_from": "2021-01-03",
        "date_to": "2021-01-10",
    },
    "period_2": {
        "date_from": "2021-02-01",
        "date_to": "2021-02-07",
    },
    "alpha": 0.05,
    "data_names": data_names,
    "metric": "active_power_w_avg",
}
```

### 4.5. urls.py

Изменить `apps/consumption/urls.py`:

```python
from .analytics_views import PeriodComparisonView

urlpatterns = [
    ...
    path("analytics/period-comparison/", PeriodComparisonView.as_view(), name="analytics-period-comparison"),
]
```

Итоговый frontend URL:

```text
/api/consumption/analytics/period-comparison/
```

### 4.6. Тесты backend

Добавить тесты в:

```text
apps/consumption/tests.py
```

Минимум:

1. Неавторизованный запрос получает `401`.
2. Отсутствующие даты дают `400`.
3. Пересекающиеся периоды дают `400`.
4. Некорректный `alpha` дает `400`.
5. При успешном запросе view вызывает `request_period_comparison` с ожидаемым payload.
6. Если analytics client возвращает ошибку недоступности, endpoint возвращает понятный `503`.

В тестах не ходить в реальный FastAPI и PostgreSQL. Мокать `request_period_comparison` и, где нужно, `_matching_data_names`.

## 5. Изменения во frontend

Рабочая папка:

```text
/Users/petrtarancenko/ВУЗ/4 семестр/Умное энергопотребление/Фронт/smart_energy
```

### 5.1. API module

Создать файл:

```text
src/features/analytics/api/analyticsApi.js
```

Можно почти полностью повторить подход из `src/features/dashboard/api/dashboardApi.js`.

Base path:

```js
const ANALYTICS_API_BASE = '/consumption/analytics'
```

Функции:

```js
function buildQuery(params = {}) { ... }

export function getPeriodComparison(params) {
  return getJson(`/period-comparison/${buildQuery(params)}`)
}
```

Нужно использовать:

- `buildApiUrl`;
- `getAccessToken`;
- `refreshAccessToken`;
- `credentials: 'include'`;
- retry при `401`, как в dashboard API.

### 5.2. Навигация

Файл:

```text
src/pages/DashboardPage.jsx
```

Изменить:

```js
const NAV_ITEMS = [
  { id: 'dashboard', label: 'Дашборд' },
  { id: 'links', label: 'Связи' },
  { id: 'analytics', label: 'Аналитика' },
]
```

В `SidebarIcon` добавить ветку для `analytics`. Можно нарисовать простую line-chart icon тем же SVG-стилем, что уже используется в файле.

### 5.3. Состояние страницы аналитики

В `DashboardPage` добавить state:

```js
const [analyticsPeriod1From, setAnalyticsPeriod1From] = useState('')
const [analyticsPeriod1To, setAnalyticsPeriod1To] = useState('')
const [analyticsPeriod2From, setAnalyticsPeriod2From] = useState('')
const [analyticsPeriod2To, setAnalyticsPeriod2To] = useState('')
const [analyticsAlpha, setAnalyticsAlpha] = useState('0.05')
const [analyticsResult, setAnalyticsResult] = useState(null)
const [analyticsLoading, setAnalyticsLoading] = useState(false)
const [analyticsError, setAnalyticsError] = useState('')
```

После загрузки `filters.date_range` установить дефолтные непересекающиеся периоды, чтобы страница сразу была пригодна к использованию:

- взять доступные даты через уже существующий `buildAvailableDates(filters.date_range)`;
- если дат минимум 14:
  - период 1: первые 7 доступных дней;
  - период 2: последние 7 доступных дней;
- если дат меньше 14:
  - не автозаполнять оба периода и показать подсказку в панели, что нужно выбрать два непересекающихся периода.

### 5.4. Клиентская валидация

Добавить helper:

```js
function validateAnalyticsPeriods({ p1From, p1To, p2From, p2To }) {
  if (!p1From || !p1To || !p2From || !p2To) return 'Заполните оба периода'
  if (p1From > p1To) return 'В первом периоде дата начала позже даты конца'
  if (p2From > p2To) return 'Во втором периоде дата начала позже даты конца'
  const nonOverlapping = p1To < p2From || p2To < p1From
  if (!nonOverlapping) return 'Периоды не должны пересекаться'
  return ''
}
```

Сравнение строк `YYYY-MM-DD` корректно, потому что формат ISO date сортируется лексикографически.

### 5.5. Запрос расчета

Добавить handler:

```js
async function handleAnalyticsCompare(event) {
  event.preventDefault()
  const validationError = validateAnalyticsPeriods(...)
  if (validationError) {
    setAnalyticsError(validationError)
    return
  }

  setAnalyticsLoading(true)
  setAnalyticsError('')

  try {
    const result = await getPeriodComparison({
      period1_from: analyticsPeriod1From,
      period1_to: analyticsPeriod1To,
      period2_from: analyticsPeriod2From,
      period2_to: analyticsPeriod2To,
      alpha: analyticsAlpha,
      data_name: selectedDataName,
      room: selectedRoom,
      consumer_class: selectedConsumerClass,
    })
    setAnalyticsResult(result)
  } catch (requestError) {
    setAnalyticsError(requestError.message)
  } finally {
    setAnalyticsLoading(false)
  }
}
```

Использовать существующие фильтры `selectedDataName`, `selectedRoom`, `selectedConsumerClass`, чтобы аналитика сравнивала тот же контекст, что и дашборд.

### 5.6. Разметка страницы

Внутри `energy-main` добавить третий section:

```jsx
<section className={activeView === 'analytics' ? 'energy-view active' : 'energy-view'}>
  ...
</section>
```

Структура:

1. `energy-topbar`
   - `h1`: `Аналитика`
   - без длинного маркетингового описания;
   - toolbar с фильтрами `Счетчик`, `Помещение`, `Класс`, как на дашборде.
2. `energy-panel energy-analysis-form`
   - два блока периодов;
   - каждый блок содержит `input type="date"` для начала и конца;
   - `select` для уровня значимости:
     - `0.10`
     - `0.05`
     - `0.01`
   - кнопка `Сравнить`.
3. После результата:
   - `energy-kpi-grid` из 4 карточек:
     - `z статистическое`;
     - `z критическое`;
     - `alpha`;
     - `решение`.
   - две панели со сводкой периодов:
     - даты;
     - средняя мощность;
     - суммарная энергия;
     - наблюдения;
     - стандартное отклонение.
   - панель `Вывод`, где показать conclusion из API.

Форматирование:

- z-значения: `formatNumber(value, 3)`;
- кВт: `formatNumber(value, 2)`;
- кВт·ч: `formatNumber(value, 0)`;
- observations/source points: `formatNumber(value, 0)`.

Если `analyticsResult.reject_null === true`, карточка решения должна визуально использовать danger/accent-danger.

Если `false`, использовать primary/success.

### 5.7. CSS

Файл:

```text
src/dashboard.css
```

Добавить только недостающие классы, опираясь на текущие CSS variables:

```css
.energy-analysis-form { ... }
.energy-analysis-periods { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.energy-analysis-period { ... }
.energy-analysis-actions { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 12px; }
.energy-analysis-result { ... }
.energy-analysis-decision { ... }
```

Не менять глобальный visual style приложения. Цвета брать из существующих CSS variables:

- `--energy-primary`
- `--energy-danger`
- `--energy-warning`
- `--energy-text-secondary`
- `--energy-surface`
- `--energy-border`

Responsive:

```css
@media (max-width: 1180px) {
  .energy-analysis-periods {
    grid-template-columns: 1fr;
  }
}
```

Сейчас `.energy-nav` на mobile имеет `repeat(2, ...)`. После добавления третьей кнопки лучше изменить на:

```css
.energy-nav {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}
```

в media block `max-width: 960px`. Проверить, что текст `Аналитика` не переносится некрасиво.

## 6. Контракт ответа API для frontend

Пример успешного ответа:

```json
{
  "hypothesis": "H0: среднее потребление в двух периодах одинаковое",
  "alternative": "two_sided",
  "alpha": 0.05,
  "metric": "active_power_w_avg",
  "unit": "kW",
  "periods": [
    {
      "key": "period_1",
      "date_from": "2021-01-03",
      "date_to": "2021-01-10",
      "actual_from": "2021-01-03T00:00:00+03:00",
      "actual_to": "2021-01-10T23:00:00+03:00",
      "observations": 168,
      "source_points": 92340,
      "mean_kw": 12.431,
      "stddev_kw": 2.104,
      "variance_kw2": 4.426,
      "total_energy_kwh": 2088.4
    },
    {
      "key": "period_2",
      "date_from": "2021-02-01",
      "date_to": "2021-02-07",
      "actual_from": "2021-02-01T00:00:00+03:00",
      "actual_to": "2021-02-07T23:00:00+03:00",
      "observations": 168,
      "source_points": 91801,
      "mean_kw": 13.072,
      "stddev_kw": 2.411,
      "variance_kw2": 5.813,
      "total_energy_kwh": 2196.1
    }
  ],
  "difference_mean_kw": -0.641,
  "standard_error": 0.247,
  "z_statistic": -2.595,
  "z_critical": 1.96,
  "reject_null": true,
  "conclusion": "Нулевая гипотеза отвергается: среднее потребление в выбранных периодах статистически значимо различается при alpha = 0.05.",
  "table_lookup": {
    "source": "NIST Engineering Statistics Handbook standard normal table",
    "target_cdf": 0.975,
    "matched_z": 1.96,
    "matched_cdf": 0.975,
    "alpha": 0.05
  }
}
```

Frontend не должен самостоятельно пересчитывать статистику. Он только отображает результат API.

## 7. Обработка ошибок

FastAPI:

- `422`: невалидные даты, пересекающиеся периоды, недостаточно наблюдений, `alpha` вне диапазона.
- `503`: ошибка подключения к PostgreSQL.
- `500`: непредвиденная ошибка, без раскрытия SQL/password.

Django:

- `400`: ошибка query params или фильтров.
- `401`: пользователь не авторизован.
- `503`: FastAPI недоступен или timeout.
- `502`: FastAPI вернул неожиданный ответ.

Frontend:

- показывать ошибку в существующем `energy-alert`;
- кнопку `Сравнить` блокировать при `analyticsLoading`;
- не очищать прошлый успешный результат, если новый запрос упал, чтобы пользователь видел предыдущий расчет и ошибку сверху.

## 8. Проверка реализации

### 8.1. FastAPI unit tests

Запуск из `Аналитический сервис/Analytical_service`:

```bash
pytest
```

Покрыть:

1. `alpha=0.05` возвращает `z_critical=1.96`.
2. `alpha=0.01` возвращает табличное значение около `2.58`.
3. Пересекающиеся периоды дают validation error.
4. Период с `date_from > date_to` дает validation error.
5. Два набора period stats дают корректный `z_statistic`.
6. `observations < 2` дает понятную ошибку.
7. При заданном `ANALYTICS_INTERNAL_TOKEN` запрос без токена получает `401` или `403`.

### 8.2. Backend tests

Запуск из `Бэк/project_backend`:

```bash
python manage.py test apps.consumption
```

Покрыть пункты из раздела 4.6.

### 8.3. Frontend checks

Запуск из `Фронт/smart_energy`:

```bash
npm run build
npm run lint
```

Ручная проверка:

1. Запустить SSH-туннель к PostgreSQL, как описано в корневом `LOCAL_RUN.md`.
2. Запустить FastAPI:

```bash
cd "/Users/petrtarancenko/ВУЗ/4 семестр/Умное энергопотребление/Аналитический сервис/Analytical_service"
uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload
```

3. Запустить backend:

```bash
cd "/Users/petrtarancenko/ВУЗ/4 семестр/Умное энергопотребление/Бэк/project_backend"
./run_local_backend.sh
```

4. Запустить frontend:

```bash
cd "/Users/petrtarancenko/ВУЗ/4 семестр/Умное энергопотребление/Фронт/smart_energy"
npm run dev
```

5. Открыть Vite URL.
6. Войти в приложение.
7. Проверить, что в sidebar появились `Дашборд`, `Связи`, `Аналитика`.
8. Открыть `Аналитика`.
9. Выбрать два непересекающихся периода.
10. Нажать `Сравнить`.
11. Убедиться, что отображаются z статистическое, z критическое, alpha и вывод.
12. Выбрать пересекающиеся периоды и убедиться, что запрос блокируется или возвращает понятную ошибку.

## 9. Порядок реализации для слабого агента

1. Проверить ветки в трех рабочих деревьях.
2. Создать skeleton FastAPI-сервиса в `Аналитический сервис/Analytical_service`.
3. Заполнить `requirements.txt`, `.env.example`, `README.md`.
4. Переписать z-таблицу NIST в `app/data/standard_normal_cdf.csv`.
5. Реализовать `config.py`, `security.py`, `z_table.py`.
6. Реализовать Pydantic-схемы периода и ответа.
7. Реализовать PostgreSQL repository/query для period stats.
8. Реализовать расчет z-теста.
9. Реализовать FastAPI routes.
10. Написать FastAPI tests и добиться прохождения `pytest`.
11. В backend добавить settings для analytics service.
12. В backend добавить `analytics_client.py`.
13. В backend добавить `analytics_views.py`.
14. Подключить backend URL.
15. Добавить backend tests с моками.
16. Во frontend добавить `analyticsApi.js`.
17. Добавить nav item `Аналитика` и icon.
18. Добавить state, validation и handler сравнения в `DashboardPage.jsx`.
19. Добавить JSX страницы аналитики.
20. Добавить CSS для аналитики.
21. Запустить `npm run build` и `npm run lint`.
22. Запустить все три сервиса локально и проверить сценарий вручную.
23. Если на любом шаге возникает несоответствие схемы БД, сначала адаптировать SQL к реальным колонкам агрегированной таблицы, затем обновить tests и README.

## 10. Важные решения, которые нельзя менять без причины

- Не обращаться из React напрямую в FastAPI.
- Не вычислять `z_critical` через scipy/math-функции; брать значение из CSV-таблицы.
- Не использовать сырые минутные измерения для первого релиза; брать часовые агрегаты `electricity_sensor_readings_hourly`.
- Не смешивать авторизацию FastAPI с пользовательским JWT; пользовательскую авторизацию делает Django Gateway.
- Не добавлять тяжелые библиотеки для статистики ради одного z-теста.
- Не менять существующие dashboard endpoints.
- Не ломать текущий стиль `DashboardPage`; новая страница должна использовать существующие CSS variables и layout classes.
