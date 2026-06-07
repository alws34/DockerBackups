from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from app.core.context import BackupResult


class BackupDestination(ABC):
    @abstractmethod
    def upload(self, file_path: Path, result: BackupResult) -> None:
        pass
