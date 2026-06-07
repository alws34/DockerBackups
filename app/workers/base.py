from __future__ import annotations

import logging
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from app.core.context import BackupContext, BackupError, BackupResult

logger = logging.getLogger(__name__)


@dataclass
class EnvVarSpec:
    key: str
    label: str
    description: str
    secret: bool
    required: bool
    option_key: str = ""  # key in service_config["options"] pointing to this env var name


class BackupWorker(ABC):
    worker_type: str = ""
    display_name: str = ""
    description: str = ""
    env_var_specs: list[EnvVarSpec] = []

    def __init__(self, service_config: dict) -> None:
        self.service_config = service_config
        self.service_name: str = service_config["name"]
        self.options: dict = service_config.get("options", {})

    @abstractmethod
    def run(self, context: BackupContext) -> BackupResult:
        pass

    def service_backup_dir(self, context: BackupContext) -> Path:
        return context.backup_root / self.service_name

    def service_state_dir(self, context: BackupContext) -> Path:
        return context.state_root / self.service_name

    def require_option(self, key: str) -> str:
        value = self.options.get(key)
        if not value:
            raise BackupError(f"Missing required option '{key}' for service '{self.service_name}'")
        return value

    def require_env_by_option(self, context: BackupContext, option_key: str) -> str:
        env_var_name = self.require_option(option_key)
        value = context.env.get(env_var_name)
        if not value:
            raise BackupError(
                f"Missing required env var '{env_var_name}' "
                f"(referenced by option '{option_key}' in service '{self.service_name}')"
            )
        return value

    def require_binary(self, binary_name: str) -> str:
        import shutil
        path = shutil.which(binary_name)
        if not path:
            raise BackupError(f"Required binary '{binary_name}' not found in PATH")
        return path

    def cleanup_old_files(self, directory: Path, pattern: str, retention_days: int) -> None:
        cutoff = datetime.now() - timedelta(days=retention_days)
        for f in directory.glob(pattern):
            if f.is_file() and datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                f.unlink()
                logger.info(f"Deleted old backup: {f.name}")

    def run_command(
        self,
        command: list[str],
        env: dict[str, str] | None = None,
        check: bool = True,
        capture: bool = True,
        redacted_command: list[str] | None = None,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess:
        log_cmd = redacted_command or command
        logger.debug(f"Running: {' '.join(log_cmd)}")
        result = subprocess.run(
            command,
            env=env,
            capture_output=capture,
            text=True,
            cwd=str(cwd) if cwd else None,
        )
        if check and result.returncode != 0:
            raise BackupError(
                f"Command failed (exit {result.returncode}): {' '.join(log_cmd)}\n"
                f"stderr: {result.stderr.strip()}"
            )
        return result
