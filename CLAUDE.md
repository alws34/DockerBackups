# CLAUDE.md — Homelab Takeout

Guidance for Claude Code when working in this repository.

## Project at a Glance

Homelab Takeout: scheduled API-based exports of self-hosted services. FastAPI + asyncio web server,
vanilla JS GUI, Python 3.12 Alpine. Workers produce files; destinations upload them.

## Key Invariants — Never Break These

- `PYTHONPATH=/srv` inside Docker, `PYTHONPATH=.` locally — required for `app.*` imports.
- `.env` is `chmod 600` enforced on every write; never relax this.
- **User data must survive updates.** Everything a user creates (settings, secrets, login
  tokens, run history, backups) lives in `.env`, `config/`, `state/`, `backups/`, `logs/`:
  bind-mounted and git-ignored. Never ship a tracked file the app writes to, and never
  write user data anywhere else. `tests/test_user_data.py` enforces the ignore rules.
- Secrets live ONLY in `.env` or mounted credential files — never in Python source or JSON config.
- Workers run in `asyncio.to_thread` (blocking); never call blocking I/O directly in `async def`.
- The container runs as non-root (uid 1000 / `PUID`) with a read-only root filesystem and all
  capabilities dropped. The app may only write to the bind mounts (`/backups`, `/logs`,
  `/state`, `/config`, `/app-env/.env`), `/tmp` and `$HOME` (both tmpfs).
- All tests must pass (79 at last count). Run `PYTHONPATH=. pytest tests/ -x -q` before any commit.

## Branches

- `main` is what users clone and what releases are tagged from. Never commit or push to
  it directly; it only changes through a pull request from `dev` once `dev` is tested.
- `dev` is the integration branch. Work happens on a feature branch (`feat/…`, `fix/…`,
  `ci/…`), which is merged into `dev` and pushed. CI and CodeQL run on `dev`.
- Outside contributors open PRs against `dev`. Dependabot also targets `dev`.

## Stack

| Layer       | Tech                         |
|-------------|------------------------------|
| Runtime     | Python 3.12-alpine in Docker |
| API         | FastAPI + uvicorn             |
| Scheduling  | asyncio (no external queue)  |
| GUI         | Vanilla JS, no build step    |
| Deps pinned | `requirements.txt` + `requirements-dev.txt` (pip-tools, hashed) |
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
    base.py         — BackupDestination ABC + shared retention/secret helpers
    registry.py     — ALL_DESTINATIONS and the enabled check
    *.py            — google_drive, onedrive, sftp, smb, local_folder
  api/
    server.py       — FastAPI app factory (middleware order: auth guard inside security guards)
    auth.py         — admin password (scrypt), sessions, lockout, proxy/off modes
    security.py     — Host allow-list, cross-site write blocking, security headers
    routes/         — one file per API group (services, logs, env_vars, destinations, settings)
    static/         — index.html, app.js, style.css (no build step, strict CSP: no inline JS or CSS)
config/
  services.json     — schedule, retention, service list, destination config (tracked in git)
tests/              — pytest unit tests; no live network or Docker required
```

## Adding a Worker

1. Create `app/workers/<name>.py` inheriting `BackupWorker`.
2. Set class attributes: `worker_type`, `display_name`, `description`, `env_var_specs`.
3. Implement `run(context: BackupContext) -> BackupResult`. Read settings only through
   `context.env` / `require_env` with the plain spec keys: for a second instance of the
   app the scheduler maps its `<KEY>__<n>` values onto those keys. Name every output file
   `f"{self.service_name}_{timestamp}..."` (or use `archive_json()`), never the app name:
   retention only prunes files starting with the service's own name.
4. Register in `app/core/registry.py → create_default_registry()`.
5. Add the app's logo as `app/api/static/icons/<worker_type>.svg` (or `.png`, ≤ 96 px).
   Take it from [dashboard-icons](https://github.com/homarr-labs/dashboard-icons)
   (`svg/<app>.svg`), never hotlink it. `tests/test_icons.py` fails without one.
6. Users add it from the GUI's app picker (**+ Add a service**); no `services.json` edit needed.

The GUI loads metadata dynamically — no frontend changes needed.

## Adding a Destination

1. Create `app/destinations/<name>.py` subclassing `BackupDestination`
   (`app/destinations/base.py`): `from_env`, `put`, `list_names`, `remove`, `check`.
   Retention (`ship` + `names_to_prune`) is shared; never delete files that don't
   match `<service>_...`.
2. Declare settings as `env_var_specs` (secrets `secret=True`); store tokens/keys
   only via `write_private()` under `state_dir(env)`.
3. Add it to `ALL_DESTINATIONS` in `app/destinations/registry.py`, add its logo as
   `app/api/static/icons/<destination_type>.svg` (same source as worker logos) and
   write `docs/destinations/<name>.md`. The GUI renders it automatically.

## Environment Variables

All runtime secrets are loaded from `.env`. See `.env.example` for the full list.
Never add secrets to `config/services.json` — it is committed to git.

## Dependency Management

- **Runtime deps:** edit `requirements.in` (human constraints, `>=`).
- **Test/lint deps:** edit `requirements-dev.in` (pytest, pytest-asyncio, pytest-cov, ruff).
  It starts with `-c requirements.txt`, so dev tools never shift runtime pins.
- **Regenerate both hashed lock files** (runtime first):
  ```bash
  pip-compile --generate-hashes --strip-extras requirements.in -o requirements.txt
  pip-compile --generate-hashes --strip-extras requirements-dev.in -o requirements-dev.txt
  ```
  `--strip-extras` is required: pip rejects extras in a constraints file.
- **Install** always with hashes: `pip install --require-hashes -r requirements.txt -r requirements-dev.txt`.
- Docker installs `requirements.txt` with `--require-hashes`; every dep must ship a musllinux
  wheel (no compiler in the image).
- Bitwarden CLI: version in `bw/package.json`, transitive deps locked in `bw/package-lock.json`
  (Docker runs `npm ci`). Regenerate the lock inside the pinned base image with
  `npm install --package-lock-only`.
- Base image is pinned by digest; workflow actions are pinned by commit SHA. Dependabot
  updates all of these weekly.

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

## Running as a systemd Service (no Docker)

`sudo ./scripts/install.sh [--with-bitwarden]` installs to `/opt/homelab-takeout` (code + venv),
`/etc/homelab-takeout` (config), `/var/lib/homelab-takeout` (data), using the hardened unit
`scripts/homelab-takeout.service`. Also `--upgrade`, `--uninstall [--purge]`. Logs:
`journalctl -u homelab-takeout -f`. Keep the unit at "OK" or better: `scripts/check-service-hardening.sh`
(needs systemd-analyze >= 250, e.g. in a `debian:trixie` container). Do not add
`MemoryDenyWriteExecute=yes`: it crashes the Node.js-based Bitwarden CLI.

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
