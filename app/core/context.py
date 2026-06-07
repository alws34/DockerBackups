from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


class BackupError(Exception):
    pass


@dataclass
class BackupContext:
    backup_root: Path
    log_root: Path
    state_root: Path
    retention_days: int
    env: dict[str, str]

    @classmethod
    def from_environment(cls, retention_days: int) -> BackupContext:
        return cls(
            backup_root=Path(os.environ.get("BACKUP_ROOT", "/backups")),
            log_root=Path(os.environ.get("LOG_ROOT", "/logs")),
            state_root=Path(os.environ.get("STATE_ROOT", "/state")),
            retention_days=retention_days,
            env=dict(os.environ),
        )


@dataclass
class BackupResult:
    service_name: str
    worker_type: str
    success: bool
    message: str
    output_files: list[Path]
    started_at: datetime
    finished_at: datetime
