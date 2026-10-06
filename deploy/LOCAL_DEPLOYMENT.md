# Локальный запуск в контейнерах

Ветка `local-develop` основана на `feature/vm-deployment` и сохраняет её
функциональность и готовую сборку React. Требуются Docker Desktop в режиме
Linux-контейнеров и Docker Compose. Примеры ниже — для PowerShell на Windows.
Команда `docker-compose` совместима с Compose 1.29.2; для плагина Compose
можно использовать `docker compose` с теми же аргументами.

## Запуск

В отдельном терминале компьютера откройте SSH-туннель и оставьте его работающим:

```powershell
ssh -N -L 127.0.0.1:15432:localhost:5432 restricted_user@193.232.208.50
```

Из корня проекта:

```powershell
docker-compose up -d --build
docker-compose ps
```

Откройте `https://localhost` или `https://127.0.0.1`. Без действующей сессии
появится страница входа, с сессией — дашборд. Для самоподписанного сертификата
потребуется разрешить переход в браузере либо доверить сертификат на компьютере.

Nginx отдаёт React и направляет `/api/` в `backend:5000`. Django обращается
к `analytical:8001`. Контейнеры используют обычную сеть Docker; SSH-туннель
доступен им через `host.docker.internal:15432`. Порты HTTPS, Django и FastAPI
опубликованы только на `127.0.0.1`, поэтому приложение доступно с этого компьютера.

## Настройки и сертификат

Compose уже читает заполненные `.env.example` обоих Python-сервисов. Повторно
вводить параметры БД и токен аналитики не требуется. Адрес БД переопределён
в Compose для доступа к туннелю снаружи контейнеров. При необходимости можно
указать пути к существующим настройкам:

```powershell
$env:BACKEND_ENV_FILE = './Бэк/project_backend/.env'
$env:ANALYTICS_ENV_FILE = './Аналитический сервис/Analytical_service/.env'
```

До первого запуска должны существовать файлы `ssl/nginx.crt` и `ssl/nginx.key`.
При переключении веток уже существующие файлы сохраняются. Для другого расположения
задайте `TLS_CERTIFICATE_PATH` и `TLS_KEY_PATH`. Если сертификата ещё нет, создайте
локальный сертификат с помощью OpenSSL (в Git for Windows он находится в
`C:\Program Files\Git\usr\bin\openssl.exe`):

```powershell
New-Item -ItemType Directory -Force ssl
& 'C:\Program Files\Git\usr\bin\openssl.exe' req -x509 -nodes -days 365 -newkey rsa:2048 -keyout ssl/nginx.key -out ssl/nginx.crt -subj '/CN=localhost' -addext 'subjectAltName=DNS:localhost,IP:127.0.0.1'
```

Ключ и сертификат не включаются в Git. Генерируйте их только при отсутствии
существующих файлов. HTTPS сохраняет работу защищённой cookie обновления сессии.
Ключ Django автоматически сохраняется в томе `smart-energy-local_django-secret`.

## Порты, проверка и обновление

По умолчанию используются 443, 5000 и 8001. Если они заняты другой версией
приложения, выберите свободные порты и отдельное имя проекта:

```powershell
$env:HTTPS_PORT = '8443'
$env:BACKEND_PORT = '15000'
$env:ANALYTICS_PORT = '18001'
docker-compose -p smart-energy-local up -d --build
```

В этом случае откройте `https://localhost:8443` и используйте то же
`-p smart-energy-local` во всех следующих командах Compose.

```powershell
docker-compose logs --tail=100
curl.exe -k -I https://localhost/
curl.exe -k -i https://localhost/api/auth/me/
docker-compose down
docker-compose up -d --build
```

`/api/auth/me/` без JWT возвращает `401`. При старте Django ожидает PostgreSQL
и применяет миграции. Не добавляйте `-v` к обычной остановке: том хранит ключ сессий.

## Обновление frontend

Dockerfile распаковывает `Фронт/smart_energy/frontend-dist.tar.gz` автоматически;
вручную распаковывать архив и устанавливать npm-зависимости для запуска не нужно.
После изменения интерфейса обновите готовую сборку (требуется Node.js 22):

```powershell
Set-Location 'Фронт/smart_energy'
npm ci
npm run lint
$env:VITE_API_URL = '/api'
npm run build
tar -czf frontend-dist.tar.gz -C dist .
```

Включайте обновлённый архив в коммит вместе с исходниками интерфейса.

## Linux без Docker Desktop

На обычном Docker Engine `host.docker.internal` указывает на шлюз Docker.
Туннель, слушающий только Linux-loopback, через него недоступен. Определите
адрес шлюза командой `docker network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}'`
и привяжите SSH-туннель к этому адресу вместо `127.0.0.1`. Например, для шлюза
`172.17.0.1` используйте `ssh -N -L 172.17.0.1:15432:localhost:5432 restricted_user@193.232.208.50`.
Остальные команды Compose остаются теми же.
