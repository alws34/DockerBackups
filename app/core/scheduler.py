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


class BackupScheduler:
    def __init__(self, config_file: str, registry: WorkerRegistry, env_manager: EnvManager | None = None) -> None:
        self.config_file = config_file
        self.registry = registry
        self.env_manager = env_manager
        self._config: dict[str, Any] = {}
        self._running: dict[str, bool] = {}

    def load_config(self) -> None:
        with open(self.config_file) as f:
            self._config = json.load(f)

    def get_config(self) -> dict[str, Any]:
        return self._config

    def get_settings(self) -> dict:
        return {
            "daily_at": self._config.get("schedule", {}).get("daily_at", "03:30"),
            "interval_hours": self._config.get("schedule", {}).get("interval_hours", 0),
            "run_on_start": self._config.get("schedule", {}).get("run_on_start", False),
            "keep_days": self._config.get("retention", {}).get("keep_days", 30),
        }

    def update_settings(self, daily_at: str, interval_hours: int, run_on_start: bool, keep_days: int) -> None:
        self._config.setdefault("schedule", {})["daily_at"] = daily_at
        self._config.setdefault("schedule", {})["interval_hours"] = interval_hours
        self._config.setdefault("schedule", {})["run_on_start"] = run_on_start
        self._config.setdefault("retention", {})["keep_days"] = keep_days
        with open(self.config_file, "w") as f:
            json.dump(self._config, f, indent=2)
            f.write("\n")

    def set_enabled(self, service_name: str, enabled: bool) -> None:
        services = self._config.get("services", [])
        svc = next((s for s in services if s["name"] == service_name), None)
        if svc is None:
            raise KeyError(service_name)
        svc["enabled"] = enabled
        with open(self.config_file, "w") as f:
            json.dump(self._config, f, indent=2)
            f.write("\n")

    def is_running(self, service_name: str) -> bool:
        return self._running.get(service_name, False)

    def get_state(self, service_name: str) -> dict | None:
        state_file = (
            Path(os.environ.get("STATE_ROOT", "/state"))
            / service_name
            / "last_result.json"
        )
        if state_file.exists():
            try:
                return json.loads(state_file.read_text())
            except Exception:
                return None
        return None

    async def run_forever(self) -> None:
        self.load_config()
        if self._config.get("schedule", {}).get("run_on_start", False):
            await self._run_all_services()
        while True:
            seconds = self._seconds_until_next_run()
            logger.info(f"Next scheduled backup in {seconds / 3600:.1f} hours")
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
        name = service_config["name"]
        if self._running.get(name):
            logger.warning(f"[{name}] Already running, skipping")
            return None
        self._running[name] = True
        try:
            worker = self.registry.create(service_config)
            context = self._make_context()
            loop = asyncio.get_event_loop()
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
        for file_path in result.output_files:
            try:
                destination.upload(file_path, result)
            except Exception as e:
                logger.error(f"[{result.service_name}] Google Drive upload failed for {file_path.name}: {e}")

    def _make_context(self) -> BackupContext:
        retention = self._config.get("retention", {}).get("keep_days", 30)
        # Merge os.environ with live .env file so changes take effect without container restart
        env = dict(os.environ)
        if self.env_manager:
            env.update(self.env_manager.read())
        return BackupContext(
            backup_root=Path(env.get("BACKUP_ROOT", "/backups")),
            log_root=Path(env.get("LOG_ROOT", "/logs")),
            state_root=Path(env.get("STATE_ROOT", "/state")),
            retention_days=retention,
            env=env,
        )

    def _seconds_until_next_run(self) -> float:
        schedule = self._config.get("schedule", {})
        interval_hours = schedule.get("interval_hours", 0)
        if interval_hours and interval_hours > 0:
            return interval_hours * 3600
        daily_at = schedule.get("daily_at", "03:30")
        h, m = map(int, daily_at.split(":"))
        now = datetime.now()
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return (target - now).total_seconds()

    def _persist_state(self, service_name: str, result: BackupResult) -> None:
        state_dir = Path(os.environ.get("STATE_ROOT", "/state")) / service_name
        state_dir.mkdir(parents=True, exist_ok=True)
        state_file = state_dir / "last_result.json"
        state_file.write_text(
            json.dumps(
                {
                    "success": result.success,
                    "message": result.message,
                    "started_at": result.started_at.isoformat(),
                    "finished_at": result.finished_at.isoformat(),
                    "output_files": [str(f) for f in result.output_files],
                },
                indent=2,
            )
        )

    def _persist_error_state(self, service_name: str, error: str) -> None:
        state_dir = Path(os.environ.get("STATE_ROOT", "/state")) / service_name
        state_dir.mkdir(parents=True, exist_ok=True)
        state_file = state_dir / "last_result.json"
        state_file.write_text(
            json.dumps(
                {
                    "success": False,
                    "message": error,
                    "started_at": datetime.now().isoformat(),
                    "finished_at": datetime.now().isoformat(),
                    "output_files": [],
                },
                indent=2,
            )
        )
