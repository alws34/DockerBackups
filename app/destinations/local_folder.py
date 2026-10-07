"""Copy backups into another local folder (NAS mount, rclone mount, Syncthing folder...)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import ClassVar

from app.core.context import BackupError
from app.destinations.base import BackupDestination, require
from app.workers.base import EnvVarSpec

PATH_ENV = "LOCAL_FOLDER_PATH"


class LocalFolderDestination(BackupDestination):
    """Copy each backup into ``<path>/<service>/``."""

    destination_type: ClassVar[str] = "local_folder"
    display_name: ClassVar[str] = "Local folder"
    description: ClassVar[str] = (
        "Copy backups into another folder on this machine: a NAS mount, an rclone mount, "
        "a Syncthing folder. Whatever syncs that folder is up to you."
    )
    env_var_specs: ClassVar[list[EnvVarSpec]] = [
        EnvVarSpec(
            key=PATH_ENV,
            label="Folder path",
            description="Absolute path, e.g. /mnt/nas/homelab-takeout. Must be writable.",
            secret=False,
            required=True,
        ),
    ]

    def __init__(self, root: Path) -> None:
        self.root = root

    @classmethod
    def from_env(cls, env: dict[str, str]) -> LocalFolderDestination:
        root = Path(require(env, PATH_ENV, "Local folder path"))
        if not root.is_absolute():
            raise BackupError(f"{PATH_ENV} must be an absolute path, got '{root}'.")
        return cls(root)

    def put(self, file_path: Path, service_name: str) -> None:
        target_dir = self.root / service_name
        target_dir.mkdir(parents=True, exist_ok=True)
        # Copy to a hidden temp name first so a half-written file never looks complete.
        tmp = target_dir / f".{file_path.name}.partial"
        shutil.copyfile(file_path, tmp)
        os.replace(tmp, target_dir / file_path.name)

    def list_names(self, service_name: str) -> list[str]:
        folder = self.root / service_name
        return [p.name for p in folder.iterdir() if p.is_file()] if folder.is_dir() else []

    def remove(self, service_name: str, name: str) -> None:
        (self.root / service_name / name).unlink(missing_ok=True)

    def check(self) -> str:
        self.root.mkdir(parents=True, exist_ok=True)
        if not os.access(self.root, os.W_OK):
            raise BackupError(f"{self.root} is not writable by this app.")
        return f"Writable: {self.root}"
