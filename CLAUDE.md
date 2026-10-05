# CLAUDE.md — Homelab Takeout

Guidance for Claude Code when working in this repository.

## Project at a Glance

Homelab Takeout: scheduled API-based exports of self-hosted services. FastAPI + asyncio web server,
vanilla JS GUI, Python 3.12 Alpine. Workers produce files; destinations upload them.

## Key Invariants — Never Break These

- `PYTHONPATH=/srv` inside Docker, `PYTHONPATH=.` locally — required for `app.*` imports.
- `.env` is `chmod 600` enforced on every write; never relax this.
- Secrets live ONLY in `.env` or mounted credential files — never in Python source or JSON config.
- Workers run in `asyncio.to_thread` (blocking); never call blocking I/O directly in `async def`.
- All tests must pass (44 at last count). Run `pytest tests/ -x -q` before any commit.

## Stack

| Layer       | Tech                         |
|-------------|------------------------------|
| Runtime     | Python 3.12-alpine in Docker |
| API         | FastAPI + uvicorn             |
| Scheduling  | asyncio (no external queue)  |
| GUI         | Vanilla JS, no build step    |
| Deps pinned | `requirements.txt` (pip-tools lock file) |
| Linting     | ruff (`pyproject.toml`)       |

## Directory Map

```
app/
  main.py           — entrypoint: wires scheduler + FastAPI, starts both
  core/
    context.py      — BackupContext, BackupResult, BackupError dataclasses
    registry.py     — WorkerRegistry: maps type strings → worker classes
    scheduler.py    — BackupScheduler: cron loop, state persistence, uploads
    env_manager.py  — reads/writes .env file; hot-reloaded each backup run
  workers/
    base.py         — BackupWorker ABC + shared helpers (run_command, cleanup)
    *.py            — one file per service (vaultwarden, wikijs, snipeit, …)
  destinations/
    base.py         — BackupDestination ABC
    google_drive.py — Google Drive OAuth2 upload destination
  api/
    server.py       — FastAPI app factory
    routes/         — one file per API group (services, logs, env_vars, destinations, settings)
    static/         — index.html, app.js, style.css (Glassmorphism dark theme)
config/
  services.json     — schedule, retention, service list, destination config (tracked in git)
tests/              — pytest unit tests; no live network or Docker required
```

## Adding a Worker

1. Create `app/workers/<name>.py` inheriting `BackupWorker`.
2. Set class attributes: `worker_type`, `display_name`, `description`, `env_var_specs`.
3. Implement `run(context: BackupContext) -> BackupResult`.
4. Register in `app/core/registry.py → create_default_registry()`.
5. Add entry to `config/services.json`.

The GUI loads metadata dynamically — no frontend changes needed.

## Environment Variables

All runtime secrets are loaded from `.env`. See `.env.example` for the full list.
Never add secrets to `config/services.json` — it is committed to git.

## Dependency Management

- **Edit** `requirements.in` (human constraints, `>=`).
- **Regenerate** `requirements.txt` (pinned lock file): `pip-compile requirements.in -o requirements.txt`.
- Docker uses `requirements.txt` — this guarantees bit-for-bit reproducible installs.

## Lint / Format

```bash
ruff check app/ tests/
ruff format app/ tests/
```

Config in `pyproject.toml`. CI should fail on any ruff error.

## Running Locally (no Docker)

```bash
cp .env.example .env && chmod 600 .env
# fill in .env values

PYTHONPATH=. \
  CONFIG_FILE=./config/services.json \
  BACKUP_ROOT=./backups \
  LOG_ROOT=./logs \
  STATE_ROOT=./state \
  ENV_FILE=./.env \
  WEB_PORT=8080 \
  python app/main.py
```

Open http://localhost:8080.

## Building and Running in Docker

```bash
docker compose build
docker compose up -d
docker logs -f homelab-takeout
```

## What NOT to Do

- Do not add `import os.path` — use `pathlib.Path` everywhere.
- Do not call `requests.*` or file I/O directly in `async def` — wrap with `asyncio.to_thread`.
- Do not broaden `except` clauses without a comment explaining the catch-all.
- Do not commit `backups/`, `logs/`, `state/`, or any `*.json` credential file.
- Do not add telemetry, analytics, or external pings of any kind.
