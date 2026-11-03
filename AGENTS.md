# Repository Guidelines

## Project Structure & Module Organization

- `Фронт/smart_energy/`: React 19 / Vite 8 frontend using React Router 7 and Recharts 3. Routes live in `src/app/`; screens in `src/pages/`, including `dashboard/` and `aggregation/`; HTTP clients in `src/features/*/api/`; auth state in `src/features/auth/model/`; shared API configuration and themes in `src/shared/`; charts in `src/components/charts/`. Static assets belong in `public/`; styles include `src/*.css` and `src/pages/aggregation/aggregation.css`.
- `Бэк/project_backend/`: Django 5.1 / Django REST Framework gateway. `config/` contains settings, routing, and runtime secret management. `apps/users/` handles the custom user, JWT cookies, and admin; `apps/consumption/` contains dashboard SQL, views, and the analytics client; `apps/aggregation/` proxies adapter operations. Tests include each module's `tests.py` and `apps/users/test_admin.py`. Architecture decisions are in `docs/adr/`.
- `Аналитический сервис/Analytical_service/`: FastAPI period-comparison service. `app/api/` contains routes, `app/schemas/` validation, `app/services/` statistical calculations and Z-table lookup, `app/db/` PostgreSQL access, `app/core/` settings and internal authentication, and `app/data/` the standard-normal CSV table. Tests live in `tests/`.
- `adapter/`: PostgreSQL ingestion/aggregation package and private HTTP service. `__main__.py` exposes the CLI; `mapping.py` validates source profiles and column mappings; `model.py`, `queries.py`, and `runner.py` build and replace aggregates; `schema.py` implements the explicit additive `upgrade-aggregates` command for target metric counters; `store.py` persists control state; `service.py` runs the API, worker, and scheduler. `profiles/` contains example profiles; `tests/` includes unit, integration, HTTP, and isolated Compose checks. It uses the Python standard-library HTTP server, not FastAPI.
- Root `docker-compose.yml` runs frontend, backend, and `analytical`; `docker-compose.adapter.yml` adds the adapter and gateway configuration. `agg_taables.sql` describes existing aggregate tables; it is not a Django migration or an automatic setup step.
- `docker-compose.vm42.yml` is a standalone deployment for `.42` with a dedicated PostgreSQL 17 container and source SSH tunnel to `.50`; `docker-compose.vm42-access.yml` runs on the PC for external access through SSH. Root `README.md` explains internal HTTPS and external SSH access; `deploy/vm42/README.md` covers installation. VM services use host networking with loopback ports 25433 (new database), 25432 (source tunnel), 15000 (Django), and 18001/18002 (analytics/adapter). HTTPS listens on 8443; `deploy/vm42/https-allow.conf` temporarily ends with `allow all` at the user's request, so network firewall rules must enforce external SSH-only access. Specific allow rules are retained: replacing the final directive with `deny all` restores source IP filtering and preserves loopback SSH forwarding. The listed institute networks are the observed VM subnets; additional workstation networks require administrator confirmation when restoring filtering. Do not combine these files with local application Compose files or replace the VM's existing PostgreSQL 10/CMDBuild deployment.

Use root `README.md` for the Docker quick start, `deploy/LOCAL_DEPLOYMENT.md` for deployment and frontend packaging, `DATABASE.md` for database contracts, `SOURCES_AND_AGGREGATIONS.md` for the operator workflow, and `adapter/README.md` for adapter configuration and validation. Component READMEs and ADRs include older proposals; verify behavior against current code, dependency manifests, and Compose files before relying on them.

## Architecture & Data Flow

- `apps/notifications/` is an additive Django app with personal opt-in settings, daily report snapshots, and SMTP delivery. `run_notifications` runs in a separate Compose service using the backend image. It only reads existing daily aggregates; do not change adapter formulas or treat partial energy as a full total for alerts. A baseline uses at least 7 complete days with identical meter sets in the preceding 30 calendar days; comparison is two-sided and excludes unfinished Moscow days. Reports are unique per user/day; ambiguous SMTP submissions must not be blindly retried. SMTP secrets belong in private env files. See `NOTIFICATIONS.md`.

- The browser calls Django under `/api/`; Vite proxies that prefix in development. Container Nginx terminates HTTPS, serves the SPA, and proxies `/api/`, `/admin/`, and `/static/` to Django. Django serves admin assets through WhiteNoise.
- `/api/auth/` provides registration, login, refresh, profile, and logout. Keep access tokens in the existing in-memory auth store and refresh tokens in HttpOnly cookies. Reuse feature clients, `buildApiUrl`, and the shared refresh flow; preserve `credentials: 'include'` and the single retry after a 401.
- `/api/consumption/dashboard/` reads PostgreSQL directly through Django SQL helpers. `/api/consumption/analytics/period-comparison/` resolves filters in Django and calls FastAPI's `/internal/analytics/period-comparison` through `httpx`; FastAPI reads hourly aggregates and performs the Z-test. It does not persist analysis results.
- `/api/aggregation/` forwards an allowlisted set of operations to the adapter with `X-Internal-Token`. Management operations require `is_staff` via DRF `IsAdminUser`; the capabilities endpoint is available to authenticated users. Keep server-side permission checks even when the UI hides controls.
- Django uses three database aliases: `default` for ORM/auth tables in `student` (default search path `student_schema,public`), `source` for metadata in `paradigm_db`, and `analytics` for hourly/daily aggregates in `student`. FastAPI reads aggregates via psycopg 3. The adapter reads raw `paradigm_db.public.electricity_sensor_readings`, writes `student_schema.electricity_sensor_readings_hourly` and `student_schema.electricity_sensor_readings_daily`, and stores profiles/jobs/schedule in `adapter_control`.
- Application Compose uses external PostgreSQL, normally reached through an SSH tunnel at `127.0.0.1:15432` on the host and `host.docker.internal:15432` in containers. It does not provision PostgreSQL, source metadata, or aggregate tables. Django migrations create ORM tables; `adapter init-control` separately installs the adapter control schema.

## Build, Test, and Development Commands

Run component commands from their component directory; run `python -m adapter` and Compose commands from the repository root. Quote paths containing spaces/Cyrillic names. Keep Markdown and source files in UTF-8; in Windows PowerShell use `Get-Content -Encoding UTF8` when reading them.

### Local Development

- Frontend: use Node.js 22.12+ (the locked Vite version supports `^20.19.0 || >=22.12.0`). `npm ci` installs locked dependencies; `npm run dev` starts Vite at `127.0.0.1:5173`; `npm run build` creates `dist/`; `npm run lint` runs ESLint; `npm run preview` serves the build.
- Python: create/activate a virtual environment and run `python -m pip install -r requirements.txt` for the relevant component. Backend and analytics Docker images use Python 3.11; adapter and integration-test images use Python 3.12. Dependencies are maintained per component, not in a root requirements file. Django uses DRF, SimpleJWT, psycopg, httpx, Gunicorn, and WhiteNoise; analytics uses FastAPI, Uvicorn, Pydantic Settings, and psycopg; adapter runtime dependencies are psycopg and tzdata.
- Backend: `python manage.py migrate` applies ORM migrations; `python manage.py runserver 127.0.0.1:5000` starts the gateway; `python manage.py createsuperuser` creates an administrator. Use migrations for ORM changes, not to recreate externally managed energy tables.
- Analytics: `uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload`; `/health` is the health endpoint.
- Adapter: `python -m adapter init-control` installs control tables once; `python -m adapter serve` runs the private service (default port 8002). For CLI syntax, use `python -m adapter --help`. Local adapter commands do **not** load `.env` automatically: export the required variables in the shell. A refresh without `--apply` is a dry-run; do not add `--apply` just to validate changes.

### Docker & Frontend Packaging

Follow root `README.md` to configure environment files, the SSH tunnel, and both TLS files (`ssl/nginx.crt`, `ssl/nginx.key`) before starting. The standard application URL is `https://localhost`; backend and analytics are bound to host loopback on 5000 and 8001. Adapter port 8002 is internal only.

```powershell
docker compose -f docker-compose.yml -f docker-compose.adapter.yml build
docker compose -f docker-compose.yml -f docker-compose.adapter.yml run --rm --no-deps adapter init-control
docker compose -f docker-compose.yml -f docker-compose.adapter.yml run --rm --no-deps adapter upgrade-aggregates
docker compose -f docker-compose.yml -f docker-compose.adapter.yml up -d
docker compose -f docker-compose.yml -f docker-compose.adapter.yml ps
```

`init-control` is a first-install step; `upgrade-aggregates` explicitly adds target metric counters and leaves legacy values/counts unverified until refresh. To run without the adapter, use `docker compose up -d --build`; the aggregation management page needs the overlay and the same aggregate schema. Backend startup waits for PostgreSQL and, in application Compose, applies migrations and runs Gunicorn. Energy index creation is disabled there (`RUN_ENERGY_INDEXES=0`); do not enable DDL as a routine startup fix. Preserve the `django-secret` volume when stopping: it holds the persistent session/JWT signing key; ordinary `down` should not use `-v`.

The frontend Dockerfile unpacks the tracked `frontend-dist.tar.gz`; it does **not** build React sources. After a UI change, run checks, build with `VITE_API_URL=/api`, and refresh the archive from inside `Фронт/smart_energy/`:

```powershell
$env:VITE_API_URL = '/api'
npm run build
tar -czf frontend-dist.tar.gz -C dist .
```

Include the updated archive with UI source changes intended for Docker deployment, then rebuild the frontend image. Keep loose `dist/`, `node_modules/`, and `staticfiles/` out of commits.

## Coding Style & Naming Conventions

Use four-space Python indentation, `snake_case` functions/modules, and `PascalCase` classes. Follow existing React code: two-space indentation, single quotes, and omitted semicolons. Components use `PascalCase.jsx`; helpers use `camelCase.js`. Keep HTTP logic in feature API modules and shared URLs in `src/shared/api/config.js`. ESLint flat configuration enforces JavaScript, React Hooks, and React Refresh rules; no Python formatter is configured. Follow surrounding Python quote style rather than reformatting unrelated code.

For SQL, bind values as parameters and validate/quote dynamic identifiers using existing helpers or `psycopg.sql.Identifier`. Never accept arbitrary SQL or connection strings from browser input; adapter profiles refer to server-defined connection aliases.

## Testing Guidelines

| Component / working directory | Command | Scope |
| --- | --- | --- |
| Frontend | `npm run test:mapping` | Node's built-in test runner for column-mapping visibility |
| Frontend | `npm run test:dashboard` | Missing measurements versus real zero, timeline gaps, and fallback values |
| Frontend | `npm run lint` and `npm run build` | Static checks and production build; manually verify affected screens |
| Backend | `python manage.py test apps.consumption apps.aggregation apps.users` | Django `SimpleTestCase` / DRF `APIClient`, including admin and session tests; external calls are mocked |
| Analytics | `python -m pytest` | Validation, period statistics, Z-table, API health, and saved-energy behavior |
| Repository root | `python -m unittest discover -s adapter/tests -v` | Adapter unit tests; integration tests skip without `ADAPTER_TEST_ADMIN_DSN` |
| Repository root | `docker compose -p smart-energy-adapter-tests -f adapter/tests/compose.yml run --build --rm tests` | Isolated PostgreSQL 13, adapter unit/integration tests, Django, analytics, and real SQL checks of energy consumers |

Use only an isolated test PostgreSQL for `ADAPTER_TEST_ADMIN_DSN`: fixtures create `test_adapter_source` / `test_adapter_target` and truncate their tables. Never point it at a working database server. Do not run the full adapter tests alongside the UI fixture; they reset the same test tables. See `adapter/README.md` for the `ui` profile and HTTP smoke test.

Name tests `test_*`; analytics files use `test_*.py`. Cover validation, authentication/staff access, service failures, statistical edge cases, mappings/units, missing data versus zero, and transactional/idempotent refreshes as applicable. Mock external databases/services in unit tests. No coverage threshold or general frontend component/E2E runner is configured; `test:mapping` is the existing focused frontend suite. Report the commands actually run and any skipped integration checks.

## Aggregation & Energy Contracts

- Source readings and metadata are read-only. Refreshes replace all rows for each selected day in **both** existing aggregate tables in one transaction per day, protected by target locks. Preserve rollback, idempotency, and retry/progress behavior; different source profiles cannot safely merge independent data into the same target day.
- Ranges are `[from, to)` in the configured timezone; unfinished/current and future days are excluded. Keep the Moscow output/calendar conventions. Explicitly interpret timezone-naive source timestamps through `source_timezone`.
- Profiles are versioned and require confirmed mappings, units, and sampling interval. Preserve the successful preview requirement before service apply/activation and the separate schedule enablement. Source schema changes, duplicates, inadequate coverage, or unknown sensors must not silently bypass quality checks.
- Only timestamp and meter ID are required source mappings. Missing/unreadable optional measurement columns produce NULL and schema warnings; an existing mapped column of the wrong type is an error. Missing required columns, NULL timestamps (checked across the source), and blank/NULL IDs in the requested range stop refresh before replacement. Total power needs its total reading or all three valid phases; partial phases remain individual measurements. Manual apply requires the schema signature from a fresh successful preview. See `adapter/REVIEW.md` for the checked scenarios and limitations.
- Energy uses a confirmed measurement interval and normalized power units; never guess the interval or treat the old minute-based SQL formula as a reference for 5-second readings. Missing/invalid power can produce warnings for individual meters and `NULL` energy; a real zero remains zero. Preserve unknown energy in aggregate totals rather than converting it to zero or silently summing only known values.
- Aggregate means persist `<metric>_count` (hourly `METRICS`, daily `DAILY_METRICS`). Consumers must weight by these valid-value counts, never by `points_count` or a guessed legacy count. Run `upgrade-aggregates` explicitly before the new writer/API; VM `start.sh` stops the old adapter and runs this additive target-only upgrade. Legacy counters remain NULL until a validated refresh. Fingerprint version 2 requires a new profile version and preview. Weekly power averages each meter across time before summing meters; energy remains additive.
- Power timeseries retain the strict full `value` and separately expose `available_value`, `available_meters`, and `total_meters`. The UI displays available power with explicit coverage instead of hiding all meters when one is missing; fully unknown points remain gaps and measured zero remains zero. Meter scope covers meters present anywhere in the filtered aggregate range; it cannot discover meters absent from the entire range. Energy totals remain strict. Test actual non-NULL chart values, not only HTTP status and point count.
- `ENERGY_USE_STORED_ENERGY` defaults to `0` (legacy consumption calculation). Enabling stored `energy_kwh` requires historical validation/recalculation and the same flag in Django and analytics. Follow the transition procedure in `adapter/README.md`; an adapter deployment alone does not switch consumers.
- The existing VM `.42` deployment completed this transition on 2026-10-31: both consumers use stored energy and the daily schedule is enabled. Legacy rows that could not be verified remain explicitly unverified. See `deploy/vm42/ADAPTER_UPDATE.md`; do not treat the original migration's disabled schedule/legacy flag as its current state.

## Commit & Pull Request Guidelines

Use short imperative commit subjects; no Conventional Commits scheme is established. Keep commits focused and preserve unrelated local work. PRs should describe behavior changes, link related issues, list validation performed, and include screenshots for UI changes. Explain environment, migration/control-schema, frontend archive, or historical-data requirements when relevant.

## Security & Configuration Tips

- Copy component `.env.example` files to local `.env` files without overwriting existing settings. Root `.env` controls Compose interpolation: set `BACKEND_ENV_FILE` and `ANALYTICS_ENV_FILE` to the local files, otherwise base Compose loads the examples. The adapter overlay defaults `ADAPTER_ENV_FILE` to `adapter/.env`. Compose environment entries override service env-file values; check both when diagnosing configuration.
- Match Django's `ANALYTICS_SERVICE_TOKEN` (fallback `ANALYTICS_INTERNAL_TOKEN`) to FastAPI's `ANALYTICS_INTERNAL_TOKEN`; configure this explicitly because analytics permits internal calls without a token when none is set. Match `ENERGY_ADAPTER_TOKEN` in root `.env` and adapter environment; the adapter requires at least 24 characters and authenticates every HTTP request, including `/health`. Keep internal service tokens out of the browser.
- Adapter DSNs are server environment variables: `ENERGY_AGG_SOURCE_DSN`, `ENERGY_AGG_TARGET_DSN`, optional `ENERGY_AGG_DIRECTORY_DSN`, and optional `ENERGY_ADAPTER_CONTROL_DSN`. An explicit profile `directory_connection` wins; otherwise the dedicated directory reader is used when configured, falling back to the source connection. Additional source aliases come from `ENERGY_ADAPTER_CONNECTIONS`.
- Keep credentials, `.env*` except `.env.example`, TLS private keys/certificates, local databases, virtual environments, caches, checkpoints, and `energy_aggregation/` / `adapter/state/` out of commits. Do not print DSNs or secrets in logs, documentation, or test output. Use configured runtime secrets and HTTPS cookie settings for deployed Django rather than development defaults.
- VM private files (`deploy/vm42/app.env`, `.env`, `secrets/`, `ssl/`, `backups/`) are ignored. `configure.py` prepares these once without overwriting them; its tests run with `python -m unittest discover -s deploy/vm42 -p test_configuration.py`. The migration only reads `.50` and refuses to overwrite a populated target; copied legacy raw-table data is excluded. Preserve profile fingerprint validation: migrated profiles require a fresh version/preview before activation, and scheduling starts disabled.
