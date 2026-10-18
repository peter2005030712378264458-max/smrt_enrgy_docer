# Первый запуск приложения через Docker

Инструкция относится к ветке **`local-develop`**, включая сервис адаптера и страницу **«Источники и агрегации»**. Команды выполняются в **PowerShell из корня проекта**.

## 1. Подготовить окружение

Потребуются Docker Desktop с Linux-контейнерами и Docker Compose, SSH-доступ к серверу БД и свободные порты `443`, `5000`, `8001`, `15432`. Для создания HTTPS-сертификата нужен OpenSSL — он входит в Git for Windows.

PostgreSQL в Compose не запускается: используются внешние базы `paradigm_db` и `student`. Таблицы исходных показаний, справочники и обе таблицы агрегатов должны уже существовать. Django создаёт свои таблицы миграциями; адаптер отдельно создаёт служебную схему `adapter_control`. Структура описана в [DATABASE.md](DATABASE.md).

## 2. Подготовить настройки

Создайте локальные файлы настроек, если их ещё нет. Команды сохраняют существующие файлы:

```powershell
if (-not (Test-Path 'Бэк/project_backend/.env')) { Copy-Item 'Бэк/project_backend/.env.example' 'Бэк/project_backend/.env' }
if (-not (Test-Path 'Аналитический сервис/Analytical_service/.env')) { Copy-Item 'Аналитический сервис/Analytical_service/.env.example' 'Аналитический сервис/Analytical_service/.env' }
if (-not (Test-Path 'adapter/.env')) { Copy-Item 'adapter/.env.example' 'adapter/.env' }
if (-not (Test-Path '.env')) { New-Item -ItemType File '.env' | Out-Null }
```

В корневой `.env` добавьте следующие параметры, сохранив другие нужные настройки, если файл уже существует:

```dotenv
BACKEND_ENV_FILE=./Бэк/project_backend/.env
ANALYTICS_ENV_FILE="./Аналитический сервис/Analytical_service/.env"
ENERGY_ADAPTER_TOKEN=ВАШ_СЛУЧАЙНЫЙ_ТОКЕН
ENERGY_USE_STORED_ENERGY=0
```

Случайный токен можно получить в PowerShell:

```powershell
[guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
```

Скопируйте полученную строку в `ENERGY_ADAPTER_TOKEN` **в двух файлах: корневом `.env` и `adapter/.env`**. Значения должны совпадать, длина — не менее 24 символов. Это внутренний токен связи сервисов; в браузере его вводить не требуется.

Проверьте подключения и учётные данные:

| Файл | Что настроить |
| --- | --- |
| `Бэк/project_backend/.env` | Доступ Django к `student` и справочникам `paradigm_db`: `POSTGRES_*`, при необходимости отдельные `DJANGO_POSTGRES_*`, `SOURCE_POSTGRES_*`, `ENERGY_AGG_POSTGRES_*` |
| `Аналитический сервис/Analytical_service/.env` | `ANALYTICS_POSTGRES_*` для чтения агрегатов из `student` |
| `adapter/.env` | `ENERGY_AGG_SOURCE_DSN` для чтения сырых показаний из `paradigm_db`; `ENERGY_AGG_TARGET_DSN` для чтения и записи агрегатов в `student` |

Внутри контейнеров SSH-туннель доступен по **`host.docker.internal:15432`**. В DSN адаптера используйте этот адрес, а не `127.0.0.1`. Проверьте также общий внутренний токен аналитики `ANALYTICS_INTERNAL_TOKEN`; если Django отдельно задаёт `ANALYTICS_SERVICE_TOKEN`, он должен совпадать с токеном аналитического сервиса.

**Отдельное чтение справочника.** Если пользователь сырых данных не может читать `public.sensor_directory`, раскомментируйте в `adapter/.env` и заполните:

```dotenv
ENERGY_AGG_DIRECTORY_DSN=host=host.docker.internal port=15432 dbname=paradigm_db user=student password=ВАШ_ПАРОЛЬ connect_timeout=8
```

Используйте роль с правом чтения справочника. Можно взять уже работающие учётные данные подключения Django к `paradigm_db`, если у этой роли есть нужный доступ. Без этой настройки адаптер читает справочник через подключение источника; явный `directory_connection` в профиле имеет приоритет.

Назначению адаптера нужны `SELECT`, `INSERT`, `DELETE` на обеих таблицах агрегатов. Для первой установки служебной схемы нужны права её создания; при необходимости задайте отдельный `ENERGY_ADAPTER_CONTROL_DSN`. Секреты храните в локальных `.env`, не добавляйте их в Git.

`ENERGY_USE_STORED_ENERGY=0` сохраняет текущий способ расчёта энергии в дашборде. Включение чтения сохранённой энергии требует отдельной проверки и пересчёта истории — [порядок перехода](adapter/README.md#переход-приложения-на-сохранённую-энергию).

## 3. Подготовить HTTPS-сертификат

Должны существовать **оба** файла: `ssl/nginx.crt` и `ssl/nginx.key`. Если они уже есть, используйте их. Если сертификат и ключ ещё не созданы:

```powershell
New-Item -ItemType Directory -Force ssl | Out-Null
& 'C:\Program Files\Git\usr\bin\openssl.exe' req -x509 -nodes -days 365 -newkey rsa:2048 -keyout ssl/nginx.key -out ssl/nginx.crt -subj '/CN=localhost' -addext 'subjectAltName=DNS:localhost,IP:127.0.0.1'
```

Если OpenSSL установлен в другом месте, замените путь к нему. Не перезаписывайте используемый ключ. При открытии приложения браузер попросит разрешить переход с самоподписанным сертификатом.

## 4. Открыть SSH-туннель

В отдельном терминале выполните команду и оставьте его открытым:

```powershell
ssh -N -L 127.0.0.1:15432:localhost:5432 restricted_user@193.232.208.50
```

Для другого сервера замените SSH-пользователя и адрес. Туннель должен работать всё время, пока приложение обращается к БД.

## 5. Собрать и запустить сервисы

В другом терминале из корня проекта:

```powershell
docker compose -f docker-compose.yml -f docker-compose.adapter.yml build
docker compose -f docker-compose.yml -f docker-compose.adapter.yml run --rm --no-deps adapter init-control
docker compose -f docker-compose.yml -f docker-compose.adapter.yml up -d
docker compose -f docker-compose.yml -f docker-compose.adapter.yml ps
```

`init-control` выполняется при первой установке адаптера: создаёт служебные таблицы профилей, заданий и расписания. Он не заполняет агрегаты и не создаёт справочники. Если команда завершилась ошибкой, проверьте подключения и права до запуска сервисов.

При старте backend ждёт PostgreSQL и автоматически применяет миграции Django. Устанавливать Python, виртуальное окружение и npm-зависимости на компьютере для этого запуска не нужно. Frontend распаковывается из готового архива при сборке образа.

## 6. Создать администратора и войти

После запуска backend создайте суперпользователя, если его ещё нет:

```powershell
docker compose -f docker-compose.yml -f docker-compose.adapter.yml exec backend python manage.py createsuperuser
```

Введите email, имя, фамилию и пароль. Такой пользователь сразу получает `is_staff=True` и может управлять адаптером.

- **Приложение:** [https://localhost](https://localhost) — войдите созданным пользователем.
- **Источники и агрегации:** [https://localhost/aggregation](https://localhost/aggregation) — доступны персоналу (`is_staff=True`).
- **Админка Django:** [https://localhost/admin/](https://localhost/admin/) — отдельный вход тем же суперпользователем. Здесь можно включить «Статус персонала» другому пользователю.

Если сервисы не запустились, посмотрите логи:

```powershell
docker compose -f docker-compose.yml -f docker-compose.adapter.yml logs --tail=100 backend analytical adapter frontend
```

## 7. Настроить первый пересчёт

1. На странице **«Источники и агрегации»** выберите источник и таблицу `public.electricity_sensor_readings`, нажмите **«Обследовать источник»**.
2. Примените предложения и проверьте соответствия столбцов и единицы. Для текущего источника используйте интервал **5 секунд**; множитель мощности `1` применим к значениям в ваттах.
3. Подтвердите настройки и сохраните версию профиля.
4. Выберите завершённые сутки и выполните **предварительный расчёт без записи**. Конечная дата не включается: `2026-10-17 → 2026-10-18` означает только 17 октября.
5. Проверьте результат, подтвердите замену и нажмите **«Записать агрегаты»**. Существующие строки выбранных дней заменяются в обеих таблицах.
6. Для ежедневной обработки активируйте проверенную версию и отдельно включите расписание. По умолчанию оно выключено. Основной запуск — в `00:00` по Москве, повтор вчерашнего дня — в `00:15`.

Сегодняшние незавершённые сутки исключаются. Отдельные счётчики без корректной мощности дают предупреждение: их энергия остаётся `NULL`, остальные пересчитываются. Это позволяет получить доступные данные, но полный расход всех счётчиков при пропусках неизвестен.

Подробности интерфейса и проверок — [SOURCES_AND_AGGREGATIONS.md](SOURCES_AND_AGGREGATIONS.md), настройки сервиса — [adapter/README.md](adapter/README.md).

## Повторный запуск и остановка

При повторном запуске откройте SSH-туннель и выполните:

```powershell
docker compose -f docker-compose.yml -f docker-compose.adapter.yml up -d
```

После обновления кода или настроек:

```powershell
docker compose -f docker-compose.yml -f docker-compose.adapter.yml up -d --build
```

Остановка:

```powershell
docker compose -f docker-compose.yml -f docker-compose.adapter.yml down
```

При обычной остановке не добавляйте `-v`: том хранит ключ Django для сессий. Профили, задания и агрегаты хранятся во внешнем PostgreSQL.

Для запуска только frontend, Django и аналитики без адаптера используйте `docker compose up -d --build`. Раздел управления агрегациями требует запуска с обоими Compose-файлами, как описано выше.

Другие порты, Linux и обновление сборки frontend — [deploy/LOCAL_DEPLOYMENT.md](deploy/LOCAL_DEPLOYMENT.md).
