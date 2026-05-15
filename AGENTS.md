# Repository Guidelines

## Project Structure & Module Organization

Agent_O is a jewelry retail training and operations assistant. `backend/` is the FastAPI app: `main.py` creates the application, `routers/` contains API routes, shared services sit beside them, and `tests/` contains backend regressions. `frontend/` is a static browser app served at `/frontend/`; main UI logic is in `frontend/js/`, styles are in `frontend/*.css`, tests are `frontend/*.test.js`, and vendored assets live in `frontend/vendor/`. Root knowledge-base and competition folders hold business documents; `workflow/` stores Dify workflow exports and `plan/` stores implementation notes.

## Build, Test, and Development Commands

Use PowerShell from the repository root unless noted.

- `cd backend; python -m venv .venv; .\.venv\Scripts\pip install -r requirements.txt` installs backend dependencies.
- `cd backend; .\.venv\Scripts\python.exe main.py` starts FastAPI on `127.0.0.1:8000` and serves `/frontend/`.
- `$env:UVICORN_PORT='8001'; cd backend; .\.venv\Scripts\python.exe main.py` runs on another port.
- `cd backend; .\.venv\Scripts\python.exe -m pytest tests -q` runs backend tests.
- `cd frontend; npm install` installs Node dependencies.
- `cd frontend; npm test` runs the default frontend regression set; use named scripts such as `npm run test:digital-human-preferences` for one suite.

## Coding Style & Naming Conventions

All development must follow `AGETNS.md`: keep assumptions explicit, prefer the simplest sufficient solution, make surgical changes, and verify the result. Python uses 4-space indentation, `snake_case`, and type hints where useful. Keep route handlers thin and move reusable behavior into service modules. Frontend code is CommonJS/plain browser JavaScript with 2-space indentation, `camelCase`, and stable constants near the top of `frontend/js/app.js`. Preserve existing Chinese UI and business terminology.

## Testing Guidelines

Backend tests use `unittest`, `pytest`, and FastAPI `TestClient`; name files `backend/tests/test_*.py`. Frontend tests are Node scripts named `*.test.js` and often extract functions from `frontend/js/app.js`. Add focused regressions for changed routes, service logic, UI state, and persistence behavior.

## Commit & Pull Request Guidelines

This checkout has no `.git` history, so no project-specific commit convention is available. Use concise imperative subjects, optionally scoped, such as `backend: validate Dify config` or `frontend: fix assessment scroll state`. Pull requests should include a summary, test commands run, affected pages or APIs, linked issue/task, and screenshots for visible UI changes.

## Security & Configuration Tips

Keep secrets in `backend/.env` or root `.env`; never commit Dify API keys, JWT secrets, SQLite databases, generated logs, caches, or `__pycache__/`. Set `JWT_SECRET_KEY`, Dify API/base variables, `CORS_ORIGINS`, and `DATABASE_URL` explicitly outside demo environments.
