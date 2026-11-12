# Обновление расчёта по доступным измерениям

Для существующего проекта `smart-energy-vm42` на ВМ42. Не использовать local
Compose и не переустанавливать PostgreSQL. 06.11.2026 обновление установлено
на действующую ВМ; фактические задания, резервная копия и проверки описаны
в [отчёте развёртывания](RELEASE_20261106.md). Далее — воспроизводимая
инструкция, а не указание повторять первоначальный перенос.

## Изменения и предварительная проверка

Алгоритм профиля версии 3 объединяет повторы и сворачивает статистики в окна
10 секунд. Полный справочник профиля задаёт ожидаемых счётчиков: каждому
создаются 24 часовые и одна суточная строка, даже без показаний. Неизвестные
показатели — NULL, counts отсутствующих измерений — 0. Пропуски и дубликаты
не блокируют день. Локальная ошибка дня сохраняется отдельно от успешной записи.

В обеих витринах появляется `available_energy_kwh` — доступная энергия.
`energy_kwh` остаётся полной энергией, неизвестной при пропусках. Уведомления
и Z-test не принимают частичную энергию за полную. Control-схема версии 2
содержит `profiles.attempted_until` и `day_results`; `processed_until` сохраняет
смысл непрерывной успешной записи. На недельном графике видны одиночные значения.

Перед установкой:

1. Определить фактическую версию приложения и Compose v1/v2 на ВМ, сохранить
   локальные изменения. Старый `ADAPTER_UPDATE.md` не доказывает текущее состояние.
2. Поставлять adapter, backend, analytical, frontend-архив и startup script
   одной согласованной версии. Ветка содержит также предыдущие изменения
   уведомлений: если ВМ их не содержит, проверить ORM-миграции на копии БД.
   Переноса только `runner.py` недостаточно.
3. Проверить `deploy/vm42/backups/restore-complete`, приватные env/SSH/TLS-файлы
   и тома БД/ключа Django. Без маркера завершённого переноса выяснить состояние;
   не запускать первоначальную миграцию поверх существующей БД.
4. Сохранить ID/документ активного профиля, состояние расписания и прогресс через
   интерфейс. Не выводить DSN, токены и пароли. Нужен новый профиль и preview.
5. Убедиться, что workstation adapter не пишет в общие витрины параллельно.
   Массовая история, включая восстановление 29.10 и 01–03.11, в обновление не входит.

Команды далее выполняет оператор на ВМ из каталога приложения:

Для этой версии подготовлен архив `smart-energy-available-data.tar.gz` и файл
SHA256 в `.venv/aggregation-checks/` на ПК. Он содержит код и собранный frontend,
включая изменения рабочей ветки, без приватных env/ключей, пользовательских
CSV и `deploy/vm42/https-allow.conf`. Действующие SSH/TLS и правила доступа ВМ
остаются на месте. Перед распаковкой сохранить прежний код и проверить
локальные отличия VM Compose, как указано выше.

Для следующего обновления подготовить отдельный каталог внутри
`deploy/vm42/updates/` на ВМ; не перезаписывать архив уже установленного выпуска.
Пример передачи с ПК в заранее подготовленный каталог `next-release`:

```powershell
scp .venv/aggregation-checks/smart-energy-available-data.tar.gz .venv/aggregation-checks/smart-energy-available-data.tar.gz.sha256 collective@193.232.208.42:/home/collective/smart-energy-vm42/deploy/vm42/updates/next-release/
```

```sh
cd /home/collective/smart-energy-vm42
if docker compose version >/dev/null 2>&1; then
  compose() { docker compose -p smart-energy-vm42 --env-file deploy/vm42/.env -f docker-compose.vm42.yml "$@"; }
else
  compose() { docker-compose -p smart-energy-vm42 --env-file deploy/vm42/.env -f docker-compose.vm42.yml "$@"; }
fi
compose ps
```

## Резервная копия

До замены кода сохранить прежний код отдельно и отключить расписание в UI.
Остановить writer и notifications, затем снять согласованную копию БД.
Контейнеры `db`/`source-db` и PostgreSQL 10/CMDBuild не останавливать.

```sh
umask 077
stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup="deploy/vm42/backups/available-$stamp"
mkdir -p "$backup"
compose stop adapter notifications
compose exec -T db pg_dump -U postgres -p 25433 -d student -Fc > "$backup/student-before.dump"
test -s "$backup/student-before.dump"
tar --exclude='deploy/vm42/backups' --exclude='deploy/vm42/secrets' --exclude='deploy/vm42/ssl' --exclude='*/node_modules' --exclude='*/__pycache__' -czf "$backup/code-before.tar.gz" adapter Бэк Фронт "Аналитический сервис" deploy docker-compose.vm42.yml
tar -czf "$backup/private-before.tar.gz" deploy/vm42/app.env deploy/vm42/.env deploy/vm42/secrets deploy/vm42/ssl
for service in adapter backend analytical frontend; do
  docker image tag "smart-energy-vm42-$service" "smart-energy-vm42-$service:before-available-$stamp"
done
printf '%s\n' "$stamp" > "$backup/image-stamp"
```

При отдельном control DSN дополнительно сохранить соответствующую БД.
При использовании управляемых подключений сохранить том реестра и отдельный
Fernet-ключ. Дамп БД не заменяет резервную копию ключей. Приватные файлы не
публиковать и не перезаписывать конфигурацией с ПК.

## Обновление и запуск

Перенести согласованный комплект кода, сохранив приватные файлы, сетевую
конфигурацию и тома ВМ. Frontend-архив должен соответствовать исходникам.
Не переключать stored-energy флаг: на существующей ВМ оба потребителя уже
используют сохранённую энергию. Не включать `RUN_ENERGY_INDEXES`.

Для подготовленного архива после резервной копии:

```sh
(cd deploy/vm42/updates/next-release && sha256sum -c smart-energy-available-data.tar.gz.sha256)
tar -xzf deploy/vm42/updates/next-release/smart-energy-available-data.tar.gz -C /home/collective/smart-energy-vm42
```

```sh
compose build adapter backend analytical frontend
compose run --rm --no-deps adapter upgrade-aggregates
compose run --rm --no-deps adapter upgrade-control
```

Upgrades явные, идемпотентные, используют только назначение/control.
Сырые показания не пересчитываются, legacy available-energy остаётся NULL.
Control-схема готовится до worker; DDL в `serve` отсутствует.

Следующая последовательность подходит Compose v1/v2 и обходит известную ошибку
`ContainerConfig` в v1, пересоздавая только контейнеры приложения:

```sh
compose stop frontend backend analytical adapter notifications
compose rm -f frontend backend analytical adapter notifications
compose up -d --no-build backend analytical adapter frontend
compose up -d --no-build notifications
compose ps
```

Backend применяет ORM-миграции согласно VM Compose, не создаёт витрины.
Повторные старты — обновлённый `sh deploy/vm42/start.sh`, выполняющий оба
явных upgrade до worker. Нельзя использовать старый startup script.

## Проверка и активация

1. В «Источники и агрегации» сохранить новую версию прежнего профиля.
   Подтвердить mappings, единицы, исходный интервал и московский календарь.
   Проверить весь справочник, включая молчащие устройства.
   `window_seconds=10` не заменяет реальный `sample_seconds`.
2. Выполнить preview выбранных завершённых суток. Проверить предупреждения,
   отсутствующих счётчиков, структуру и список готовых/пропущенных дней.
   Preview не меняет витрины. Нельзя обходить ошибку справочника пустым списком.
3. После проверки выполнить запись того же диапазона. Пересечение заменяет
   прежние значения, в том числе на NULL. Успешно прочитанный пустой день даёт
   NULL-строки; недоступная БД не стирает агрегаты.
4. Для M счётчиков проверить 24×M часовых и M суточных строк каждого записанного
   дня; независимо сверить counts, средние и доступную энергию с источником.
   Проверять числовые значения и NULL, а не только HTTP 200 и число строк.
5. Проверить доступную мощность, число счётчиков, подпись доступной энергии,
   реальные нули и одиночную точку недельного графика. В старой неделе
   29.10–04.11 пропуски без восстановления истории останутся пропусками.
6. Активировать новую проверенную версию, включить расписание вручную только
   для единственного выбранного writer. Проверить ближайшее задание,
   attempted_until/processed_until и список пропущенных дней.

Не переносить старый прогресс в новый профиль произвольным UPDATE. Без прогресса
автоматическая обработка нового профиля начинается со вчерашнего дня;
более старые даты остаются явным ручным действием оператора.

## Откат

1. Выключить расписание, остановить adapter/notifications.
2. Вернуть сохранённые код и приватную конфигурацию; вернуть четыре сохранённые
   метки образов на исходные имена, используя stamp из резервной копии.
3. Дополнительные nullable-поля можно оставить; разрушительный downgrade
   не нужен. Но данные и control schema_version несовместимы со старым worker:
   одного возврата образов недостаточно.
4. Восстановить дамп сначала в отдельную временную БД и проверить витрины/control.
   При новых пользовательских данных после обновления не восстанавливать весь
   `student` поверх них: согласовать перенос только затронутых таблиц/состояния
   из копии. Если новых данных нет и полный restore согласован, использовать
   штатную процедуру восстановления ВМ.
5. Проверить прежние профиль, прогресс, control schema_version, API/UI;
   расписание включать только после сверки.

Не удалять тома, не выполнять `down -v`, общую очистку Docker или restore
в исходную БД ВМ50/существующий PostgreSQL CMDBuild.
