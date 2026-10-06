# Repository Guidelines

## Project Structure & Module Organization

- `Фронт/smart_energy/`: React/Vite frontend. Routes live in `src/app/`, screens in `src/pages/`, feature APIs in `src/features/`, and shared utilities in `src/shared/`. Static assets belong in `public/`; styles are in `src/*.css`.
- `Бэк/project_backend/`: Django REST API gateway. `config/` contains settings and routing; `apps/users/` handles authentication; `apps/consumption/` handles dashboards and analytics. Tests sit in each app's `tests.py`; architecture decisions are in `docs/adr/`.
- `Аналитический сервис/Analytical_service/`: FastAPI service, organized into `app/api/`, `schemas/`, `services/`, `db/`, and `core/`, with tests in `tests/`.

The frontend calls Django; Django authenticates users and forwards internal analytics requests to FastAPI.

## Build, Test, and Development Commands

Run commands from the relevant component directory; quote paths containing spaces.

- Frontend: `npm ci` installs locked dependencies; `npm run dev` starts Vite; `npm run build` creates `dist/`; `npm run lint` runs ESLint; `npm run preview` serves the built frontend.
- Both Python services: create and activate a virtual environment, then run `python -m pip install -r requirements.txt`.
- Backend: `python manage.py migrate` applies Django migrations; `python manage.py runserver 127.0.0.1:5000` starts the gateway; `python manage.py test apps.consumption` runs consumption tests.
- Analytics: `uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload` starts FastAPI; `python -m pytest` runs its tests.

## Coding Style & Naming Conventions

Use four-space Python indentation, `snake_case` functions/modules, and `PascalCase` classes. Follow existing React code: two-space indentation, single quotes, and omitted semicolons. Components use `PascalCase.jsx`; helpers use `camelCase.js`. ESLint enforces JavaScript and React Hooks rules; no Python formatter is configured.

## Testing Guidelines

Backend tests use Django `SimpleTestCase` and DRF `APIClient`; analytics tests use pytest. Name tests `test_*`, with analytics files named `test_*.py`. Cover validation, authentication, service failures, and statistical edge cases; mock external databases/services where appropriate. No coverage threshold or frontend test runner is configured. For UI changes, run lint/build and manually verify affected screens.

## Commit & Pull Request Guidelines

History uses short imperative subjects, such as `Ignore local SQLite database`; no Conventional Commits scheme is established. Keep commits focused. PRs should describe behavior changes, link related issues, list validation performed, and include screenshots for UI changes. Explain configuration or migration requirements.

## Security & Configuration Tips

Copy component `.env.example` files to local `.env` files. Configure PostgreSQL access and matching Django/FastAPI internal tokens. Keep credentials, local databases, and generated output out of commits.
