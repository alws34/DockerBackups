"""Base classes and helpers shared by all backup workers."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tarfile
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import ClassVar

import requests

from app.core.context import BackupContext, BackupError, BackupResult

logger = logging.getLogger(__name__)


def fetch_json(session: requests.Session, method: str, url: str, **kwargs: object) -> dict | list:
    """Call an API endpoint and return its JSON body, raising ``BackupError`` on any failure."""
    try:
        resp = session.request(method, url, timeout=120, **kwargs)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        raise BackupError(f"{method} {url} failed: {e}") from e
    except ValueError as e:
        raise BackupError(f"{method} {url} returned non-JSON: {resp.text[:200]!r}") from e


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
    # Shown under an "Advanced" fold in the GUI.
    advanced: bool = False

    def describe(self, raw_value: str) -> dict[str, object]:
        """Return a UI-facing dict, masking the value when the var is secret."""
        return {
            "key": self.key,
            "label": self.label,
            "description": self.description,
            "secret": self.secret,
            "required": self.required,
            "configured": bool(raw_value),
            "value": "***" if self.secret else raw_value,
            "advanced": self.advanced,
        }


class BackupWorker(ABC):
    """Base class providing config access and common helpers for workers."""

    worker_type: ClassVar[str] = ""
    display_name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    # ClassVar so subclasses share/override the spec list without it becoming a
    # mutable instance default shared across every instance.
    env_var_specs: ClassVar[list[EnvVarSpec]] = []

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
            raise BackupError(f"Missing required option '{key}' for service '{self.service_name}'")
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

    def require_env(self, context: BackupContext, key: str) -> str:
        """Return an env var's value, raising if it is missing or empty."""
        value = context.env.get(key, "").strip()
        if not value:
            raise BackupError(f"{key} is not set")
        return value

    def archive_json(
        self, context: BackupContext, started_at: datetime, files: dict[str, object]
    ) -> BackupResult:
        """Write ``{name: data}`` as JSON files into one tar.gz and apply retention."""
        backup_dir = self.service_backup_dir(context)
        backup_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{self.service_name}_{started_at.strftime('%Y%m%d_%H%M%S')}"
        archive = backup_dir / f"{stem}.tar.gz"

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            for name, data in files.items():
                (work / f"{name}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False))
            with tarfile.open(archive, "w:gz") as tar:
                tar.add(work, arcname=stem)

        archive.chmod(0o600)
        self.cleanup_old_files(backup_dir, f"{self.service_name}_*.tar.gz", context.retention_days)

        counts = ", ".join(f"{len(v)} {k}" for k, v in files.items() if isinstance(v, list))
        return BackupResult(
            service_name=self.service_name,
            worker_type=self.worker_type,
            success=True,
            message=f"{counts or 'exported'}: {archive.name} ({archive.stat().st_size} bytes)",
            output_files=[archive],
            started_at=started_at,
            finished_at=datetime.now(),
        )

    def require_binary(self, binary_name: str) -> str:
        """Return the path to a required binary, raising if it is not on PATH."""
        path = shutil.which(binary_name)
        if not path:
            raise BackupError(f"Required binary '{binary_name}' not found in PATH")
        return path

    def cleanup_old_files(self, directory: Path, pattern: str, retention_days: int) -> None:
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
        # Blocking subprocess is intentional: workers always run inside a thread
        # executor (see BackupScheduler.run_service), so this never blocks the loop.
        result = subprocess.run(  # noqa: S603
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
