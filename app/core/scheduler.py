"""Scheduler that runs backup workers on a timer and persists their results."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.core.context import BackupContext, BackupError, BackupResult
from app.core.env_manager import EnvManager
from app.core.registry import WorkerRegistry
from app.destinations.google_drive import create_google_drive_destination

logger = logging.getLogger(__name__)

_DEFAULT_STATE_ROOT = "/state"
_DEFAULT_DAILY_AT = "03:30"
_DEFAULT_KEEP_DAYS = 30
_DEFAULT_DRIVE_KEEP_COUNT = 3
_STATE_FILENAME = "last_result.json"
_SECONDS_PER_HOUR = 3600


def _state_root() -> Path:
    """Return the root directory holding per-service state files."""
    return Path(os.environ.get("STATE_ROOT", _DEFAULT_STATE_ROOT))


class BackupScheduler:
    """Loads service config, runs workers on schedule, and tracks their state."""

    def __init__(
        self,
        config_file: str,
        registry: WorkerRegistry,
        env_manager: EnvManager | None = None,
    ) -> None:
        self.config_file = Path(config_file)
        self.registry = registry
        self.env_manager = env_manager
        self._config: dict[str, Any] = {}
        self._running: dict[str, bool] = {}

    def load_config(self) -> None:
        """Load the JSON service configuration from disk into memory."""
        self._config = json.loads(self.config_file.read_text())

    def _save_config(self) -> None:
        """Persist the in-memory configuration back to disk as pretty JSON."""
        self.config_file.write_text(json.dumps(self._config, indent=2) + "\n")

    def get_config(self) -> dict[str, Any]:
        """Return the in-memory service configuration."""
        return self._config

    def get_settings(self) -> dict:
        """Return schedule and retention settings, applying defaults."""
        schedule = self._config.get("schedule", {})
        retention = self._config.get("retention", {})
        return {
            "daily_at": schedule.get("daily_at", _DEFAULT_DAILY_AT),
            "interval_hours": schedule.get("interval_hours", 0),
            "run_on_start": schedule.get("run_on_start", False),
            "keep_days": retention.get("keep_days", _DEFAULT_KEEP_DAYS),
            "drive_keep_count": retention.get("drive_keep_count", _DEFAULT_DRIVE_KEEP_COUNT),
        }

    def update_settings(
        self,
        daily_at: str,
        interval_hours: int,
        run_on_start: bool,
        keep_days: int,
        drive_keep_count: int,
    ) -> None:
        """Update schedule and retention settings and persist them to disk."""
        schedule = self._config.setdefault("schedule", {})
        schedule["daily_at"] = daily_at
        schedule["interval_hours"] = interval_hours
        schedule["run_on_start"] = run_on_start
        retention = self._config.setdefault("retention", {})
        retention["keep_days"] = keep_days
        retention["drive_keep_count"] = drive_keep_count
        self._save_config()

    def set_enabled(self, service_name: str, enabled: bool) -> None:
        """Toggle a service's enabled flag and persist the change to disk."""
        services = self._config.get("services", [])
        svc = next((s for s in services if s["name"] == service_name), None)
        if svc is None:
            raise KeyError(service_name)
        svc["enabled"] = enabled
        self._save_config()

    def is_running(self, service_name: str) -> bool:
        """Return whether a backup for the named service is in progress."""
        return self._running.get(service_name, False)

    def get_state(self, service_name: str) -> dict | None:
        """Return the last persisted result for a service, or ``None``."""
        state_file = _state_root() / service_name / _STATE_FILENAME
        if not state_file.exists():
            return None
        try:
            return json.loads(state_file.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    async def run_forever(self) -> None:
        """Run enabled services on the configured schedule, indefinitely."""
        self.load_config()
        if self._config.get("schedule", {}).get("run_on_start", False):
            await self._run_all_services()
        while True:
            seconds = self._seconds_until_next_run()
            logger.info(f"Next scheduled backup in {seconds / _SECONDS_PER_HOUR:.1f} hours")
            await asyncio.sleep(seconds)
            await self._run_all_services()

    async def _run_all_services(self) -> None:
        services = self._config.get("services", [])
        enabled = [s for s in services if s.get("enabled", False)]
        if not enabled:
            logger.info("No enabled services to back up")
            return
        tasks = [self.run_service(s) for s in enabled]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def run_service(self, service_config: dict) -> BackupResult | None:
        """Run one service's worker in a thread, persisting and uploading results.

        Returns the ``BackupResult`` on success, or ``None`` if the service was
        already running or the run raised an error (which is logged and recorded).
        """
        name = service_config["name"]
        if self._running.get(name):
            logger.warning(f"[{name}] Already running, skipping")
            return None
        self._running[name] = True
        try:
            worker = self.registry.create(service_config)
            context = self._make_context()
            loop = asyncio.get_running_loop()
            result: BackupResult = await loop.run_in_executor(None, worker.run, context)
            self._persist_state(name, result)
            if result.success:
                logger.info(f"[{name}] Backup succeeded: {result.message}")
                await loop.run_in_executor(None, self._upload_to_destinations, result, context)
            else:
                logger.error(f"[{name}] Backup reported failure: {result.message}")
            return result
        except BackupError as e:
            logger.error(f"[{name}] BackupError: {e}")
            self._persist_error_state(name, str(e))
            return None
        except Exception as e:
            logger.exception(f"[{name}] Unexpected error: {e}")
            self._persist_error_state(name, str(e))
            return None
        finally:
            self._running[name] = False

    def _upload_to_destinations(self, result: BackupResult, context: BackupContext) -> None:
        try:
            destination = create_google_drive_destination(self._config, context.env)
        except BackupError as e:
            logger.error(f"[{result.service_name}] Google Drive config error: {e}")
            return
        if destination is None:
            return
        keep_count = self._config.get("retention", {}).get(
            "drive_keep_count", _DEFAULT_DRIVE_KEEP_COUNT
        )
        for file_path in result.output_files:
            try:
                destination.upload(file_path, result, keep_count=keep_count)
            except Exception as e:  # noqa: BLE001 - isolate per-file upload failures
                logger.error(
                    f"[{result.service_name}] Google Drive upload failed "
                    f"for {file_path.name}: {e}"
                )

    def _make_context(self) -> BackupContext:
        retention = self._config.get("retention", {}).get("keep_days", _DEFAULT_KEEP_DAYS)
        # Merge os.environ with the live .env file so edits take effect without a restart.
        env = dict(os.environ)
        if self.env_manager:
            env.update(self.env_manager.read())
        return BackupContext(
            backup_root=Path(env.get("BACKUP_ROOT", "/backups")),
            log_root=Path(env.get("LOG_ROOT", "/logs")),
            state_root=Path(env.get("STATE_ROOT", _DEFAULT_STATE_ROOT)),
            retention_days=retention,
            env=env,
        )

    def _seconds_until_next_run(self) -> float:
        schedule = self._config.get("schedule", {})
        if (interval_hours := schedule.get("interval_hours", 0)) > 0:
            return interval_hours * _SECONDS_PER_HOUR
        hour, minute = map(int, schedule.get("daily_at", _DEFAULT_DAILY_AT).split(":"))
        now = datetime.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return (target - now).total_seconds()

    def _write_state(self, service_name: str, state: dict[str, Any]) -> None:
        """Write a service's last-run state to its state file, creating dirs."""
        state_dir = _state_root() / service_name
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / _STATE_FILENAME).write_text(json.dumps(state, indent=2))

    def _persist_state(self, service_name: str, result: BackupResult) -> None:
        self._write_state(
            service_name,
            {
                "success": result.success,
                "message": result.message,
                "started_at": result.started_at.isoformat(),
                "finished_at": result.finished_at.isoformat(),
                "output_files": [str(f) for f in result.output_files],
            },
        )

    def _persist_error_state(self, service_name: str, error: str) -> None:
        now = datetime.now().isoformat()
        self._write_state(
            service_name,
            {
                "success": False,
                "message": error,
                "started_at": now,
                "finished_at": now,
                "output_files": [],
            },
        )
