"""Base classes and helpers shared by all backup workers."""

from __future__ import annotations

import logging
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from app.core.context import BackupContext, BackupError, BackupResult

logger = logging.getLogger(__name__)


@dataclass
class EnvVarSpec:
    """Describes one environment variable a worker or destination requires."""

    key: str
    label: str
    description: str
    secret: bool
    required: bool
    # Key in service_config["options"] that points to this env var's name.
    option_key: str = ""


class BackupWorker(ABC):
    """Base class providing config access and common helpers for workers."""

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
        """Perform the backup and return its result."""

    def service_backup_dir(self, context: BackupContext) -> Path:
        """Return the backup output directory for this service."""
        return context.backup_root / self.service_name

    def service_state_dir(self, context: BackupContext) -> Path:
        """Return the state directory for this service."""
        return context.state_root / self.service_name

    def require_option(self, key: str) -> str:
        """Return the named option's value, raising if it is missing or empty."""
        value = self.options.get(key)
        if not value:
            raise BackupError(
                f"Missing required option '{key}' for service '{self.service_name}'"
            )
        return value

    def require_env_by_option(self, context: BackupContext, option_key: str) -> str:
        """Resolve an option to an env var name and return that var's value.

        Raises ``BackupError`` if either the option or the referenced env var is
        missing or empty.
        """
        env_var_name = self.require_option(option_key)
        value = context.env.get(env_var_name)
        if not value:
            raise BackupError(
                f"Missing required env var '{env_var_name}' "
                f"(referenced by option '{option_key}' in service '{self.service_name}')"
            )
        return value

    def require_binary(self, binary_name: str) -> str:
        """Return the path to a required binary, raising if it is not on PATH."""
        path = shutil.which(binary_name)
        if not path:
            raise BackupError(f"Required binary '{binary_name}' not found in PATH")
        return path

    def cleanup_old_files(
        self, directory: Path, pattern: str, retention_days: int
    ) -> None:
        """Delete files matching ``pattern`` older than ``retention_days``."""
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
        """Run a subprocess, optionally logging a redacted form and raising on failure.

        When ``redacted_command`` is given it is used for logging and error
        messages so secrets in ``command`` are never written to logs.
        """
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
