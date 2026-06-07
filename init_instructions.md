# Build a Modular Docker-Based Service Backup Agent

## Goal

Build a Dockerized Python backup system that can back up multiple self-hosted services using service-specific exporters.

This should not be a naive "copy every Docker volume" solution. Full-volume backups are often redundant, large, noisy, and service-specific. The system should instead use modular backup workers where each service defines the correct backup strategy:

* API export
* CLI export
* database dump
* config export
* selected file/archive backup
* full volume backup only when actually needed

The first working implementation should support Vaultwarden daily encrypted JSON export using the Bitwarden CLI.

## Core Design Requirement

Use a modular design based on an interface/abstract class.

Each backup implementation must inherit from a common abstract base class, for example:

```python
class BackupWorker(ABC):
    @abstractmethod
    def run(self, context: BackupContext) -> BackupResult:
        pass
```

The scheduler must not know how each service is backed up.

Correct flow:

```text
Scheduler
  -> loads config
  -> reads enabled services
  -> creates worker from registry/factory
  -> calls worker.run(context)
  -> stores results/logs
```

Each concrete worker owns its own implementation details:

```text
VaultwardenEncryptedJsonWorker
  -> uses Bitwarden CLI
  -> logs in with API key
  -> unlocks using master password
  -> syncs vault
  -> exports encrypted JSON
  -> applies retention cleanup
```

Future workers should be easy to add:

```text
1. Create a new class that inherits BackupWorker
2. Register it in WorkerRegistry
3. Add it to config/services.json
```

No changes should be needed in the scheduler.

## Patterns To Use

Use these patterns:

```text
Strategy pattern:
  Each backup worker is a strategy for backing up one kind of service.

Factory/Registry pattern:
  A registry maps service type strings from config to concrete worker classes.

Context object:
  Shared runtime values are passed through BackupContext.

Result object:
  Each worker returns BackupResult.
```

## Project Structure

Create the project like this:

```text
service-backup-agent
├── Dockerfile
├── docker-compose.yml
├── .env
├── config
│   └── services.json
├── app
│   ├── main.py
│   ├── core
│   │   ├── __init__.py
│   │   ├── context.py
│   │   ├── registry.py
│   │   └── scheduler.py
│   └── workers
│       ├── __init__.py
│       ├── base.py
│       └── vaultwarden_encrypted_json.py
├── backups
├── logs
└── state
```

## Docker Requirements

The system must run inside a Docker container.

Use Python 3.12 Alpine as the base image.

The container must include:

```text
python3
nodejs
npm
Bitwarden CLI, installed globally as @bitwarden/cli
tzdata
ca-certificates
```

The container should not require Docker socket access for the first version.

Avoid mounting `/var/run/docker.sock` unless a future worker specifically requires it. Docker socket access gives the container near-root control over the host and should not be used by default.

## Dockerfile

Use this as the intended Dockerfile:

```dockerfile
FROM python:3.12-alpine

RUN apk add --no-cache \
    nodejs \
    npm \
    tzdata \
    ca-certificates \
    && npm install -g @bitwarden/cli \
    && npm cache clean --force

WORKDIR /app

COPY app/ /app/

CMD ["python", "/app/main.py"]
```

## docker-compose.yml

Use this Compose file:

```yaml
services:
  service-backup-agent:
    build:
      context: .
      dockerfile: Dockerfile

    container_name: service-backup-agent
    restart: unless-stopped

    env_file:
      - .env

    environment:
      CONFIG_FILE: /config/services.json
      BACKUP_ROOT: /backups
      LOG_ROOT: /logs
      STATE_ROOT: /state
      TZ: Asia/Jerusalem

    volumes:
      - ./config:/config:ro
      - ./backups:/backups
      - ./logs:/logs
      - ./state:/state

    networks:
      - backup-net

networks:
  backup-net:
    name: backup-net
```

## Environment Variables

Create `.env`:

```env
TZ=Asia/Jerusalem

VAULTWARDEN_URL=https://vault.your-domain.com

BW_CLIENTID=user.xxxxxxxxxxxxxxxxx
BW_CLIENTSECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

BW_PASSWORD=your-vaultwarden-master-password

VAULTWARDEN_EXPORT_PASSWORD=your-separate-backup-export-password
```

Important security notes:

* Do not reuse the Vaultwarden master password as the export password.
* `.env` must not be committed to git.
* Set permissions:

```bash
chmod 600 .env
chmod 700 backups logs state
```

## services.json

Create `config/services.json`:

```json
{
  "schedule": {
    "daily_at": "03:30",
    "run_on_start": true
  },
  "retention": {
    "keep_days": 30
  },
  "services": [
    {
      "name": "vaultwarden",
      "type": "vaultwarden_encrypted_json",
      "enabled": true,
      "options": {
        "vaultwarden_url_env": "VAULTWARDEN_URL",
        "client_id_env": "BW_CLIENTID",
        "client_secret_env": "BW_CLIENTSECRET",
        "master_password_env": "BW_PASSWORD",
        "export_password_env": "VAULTWARDEN_EXPORT_PASSWORD"
      }
    }
  ]
}
```

The scheduler should read this config and instantiate workers by `type`.

Example future service config:

```json
{
  "name": "uptime-kuma",
  "type": "sqlite_archive",
  "enabled": false,
  "options": {
    "source_path": "/sources/uptime-kuma/kuma.db"
  }
}
```

## Core Classes

### BackupContext

Create a shared context object containing:

```text
backup_root
log_root
state_root
retention_days
env
```

Use a dataclass.

It should have a helper:

```python
BackupContext.from_environment(retention_days: int) -> BackupContext
```

### BackupResult

Create a result dataclass containing:

```text
service_name
worker_type
success
message
output_files
started_at
finished_at
```

### BackupError

Create a custom exception:

```python
class BackupError(Exception):
    pass
```

## Abstract BackupWorker

Create `app/workers/base.py`.

The base class must:

* inherit from `ABC`
* define `worker_type`
* store `service_config`
* store `service_name`
* store `options`
* define abstract method `run(context)`
* provide helper methods:

  * `service_backup_dir(context)`
  * `service_state_dir(context)`
  * `require_option(key)`
  * `require_env_by_option(context, option_key)`
  * `require_binary(binary_name)`
  * `cleanup_old_files(directory, pattern, retention_days)`
  * `run_command(command, env, check=True, capture=True, redacted_command=None)`

The `run_command` helper must:

* execute subprocess commands
* support redacted logging
* capture stdout/stderr
* raise `BackupError` on failure
* never print secrets

## WorkerRegistry

Create `app/core/registry.py`.

The registry should map worker type strings to worker classes.

Example:

```python
registry.register(
    VaultwardenEncryptedJsonWorker.worker_type,
    VaultwardenEncryptedJsonWorker,
)
```

It must support:

```python
worker = registry.create(service_config)
```

If the worker type is unknown, raise an error listing known worker types.

## Scheduler

Create `app/core/scheduler.py`.

The scheduler should:

* load JSON config
* parse retention days
* create `BackupContext`
* iterate over enabled services
* create workers using registry
* call `worker.run(context)`
* log success/failure
* write failure logs to `/logs`
* support `run_on_start`
* support daily schedule using `daily_at` in `HH:MM` format
* run forever inside the container

The scheduler must not contain service-specific backup logic.

## main.py

Create `app/main.py`.

It should:

* load `CONFIG_FILE` from environment, default `/config/services.json`
* create the default registry
* create `BackupScheduler`
* call `scheduler.run_forever()`
* handle fatal errors cleanly

## First Concrete Worker: VaultwardenEncryptedJsonWorker

Create `app/workers/vaultwarden_encrypted_json.py`.

Class name:

```python
class VaultwardenEncryptedJsonWorker(BackupWorker):
    worker_type = "vaultwarden_encrypted_json"
```

Behavior:

1. Require `bw` CLI.
2. Read these env vars indirectly through options:

   * `vaultwarden_url_env`
   * `client_id_env`
   * `client_secret_env`
   * `master_password_env`
   * `export_password_env`
3. Create a service backup directory under `/backups/vaultwarden`.
4. Create a service state directory under `/state/vaultwarden`.
5. Set `BITWARDENCLI_APPDATA_DIR` to an isolated state subdirectory.
6. Run:

```bash
bw config server <vaultwarden_url>
bw logout
bw login --apikey
bw unlock --passwordenv BW_PASSWORD --raw
bw sync
bw export --format encrypted_json --password <export_password> --output <output_file>
bw lock
bw logout
```

Important:

* `bw login --apikey` authenticates the CLI.
* It does not decrypt the vault.
* `bw unlock` with the master password is still required.
* Store the returned unlock value in `BW_SESSION`.
* Use redacted logging for all sensitive commands.
* Use `--passwordenv BW_PASSWORD` for unlock.
* `bw export --password` does not have a documented `--passwordenv` equivalent, so redact the command from logs.
* After export, verify the file exists and is non-empty.
* Set backup file permissions to `0600`.
* Clean old backup files according to retention policy.
* Always lock and logout in `finally`.

Output filename format:

```text
vaultwarden_encrypted_json_YYYYMMDD_HHMMSS.json
```

## Expected Vaultwarden Backup Scope

This worker creates a logical encrypted JSON export.

It is good for:

```text
logins
secure notes
cards
identities
folders
vault items
portable restore into another Bitwarden/Vaultwarden account
```

It is not a full Vaultwarden server disaster recovery backup.

It does not fully preserve:

```text
server config
admin token/config.json
attachments unless using another export mode
Sends attachments
rsa_key files
exact server/device/session state
```

A future worker may support full Vaultwarden recovery backup separately.

## Future Worker Ideas

The architecture should allow adding:

```text
VaultwardenServerRecoveryWorker
  - SQLite backup
  - config backup
  - attachments archive
  - optional encrypted tar.gz

UptimeKumaWorker
  - SQLite backup of kuma.db

HomeAssistantWorker
  - call Home Assistant backup API
  - download backup archive

ImmichWorker
  - PostgreSQL dump
  - config backup
  - optional library metadata validation

PiHoleWorker
  - teleporter export

AdGuardWorker
  - config export

N8nWorker
  - database dump
  - workflows export
  - encryption key backup
```

## Future Notifier Interface

Design should make room for a notifier system later.

Example:

```python
class Notifier(ABC):
    @abstractmethod
    def notify(self, result: BackupResult) -> None:
        pass
```

Future notifiers:

```text
HomeAssistantMqttNotifier
TelegramNotifier
EmailNotifier
WebhookNotifier
```

Backup workers should not send notifications directly. Workers should only return `BackupResult`. The scheduler or a notification manager should send notifications.

## Build Commands

The final project should be runnable with:

```bash
cd /opt/service-backup-agent

docker compose build
docker compose up -d
docker logs -f service-backup-agent
```

Check backups:

```bash
ls -lh /opt/service-backup-agent/backups/vaultwarden
```

## Required Deliverable

Generate the full project source code for all files:

```text
Dockerfile
docker-compose.yml
config/services.json
app/main.py
app/core/context.py
app/core/registry.py
app/core/scheduler.py
app/workers/base.py
app/workers/vaultwarden_encrypted_json.py
app/core/__init__.py
app/workers/__init__.py
.gitignore
README.md
```

The implementation must be complete, runnable, and production-oriented.

Do not omit code.

Do not provide pseudocode.

Use clean Python 3.12 code with type hints, readable structure, and safe logging.

Secrets must never be printed.
