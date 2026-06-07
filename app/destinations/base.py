"""Abstract base class for backup upload destinations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from app.core.context import BackupResult


class BackupDestination(ABC):
    """Interface for uploading produced backup files to an external store."""

    @abstractmethod
    def upload(self, file_path: Path, result: BackupResult) -> None:
        """Upload a single backup file produced by the given result."""
