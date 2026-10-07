"""All upload destinations, and which ones are switched on."""

from __future__ import annotations

from app.destinations.base import BackupDestination, env_flag
from app.destinations.google_drive import GoogleDriveDestination
from app.destinations.local_folder import LocalFolderDestination
from app.destinations.onedrive import OneDriveDestination
from app.destinations.sftp import SftpDestination
from app.destinations.smb import SmbDestination

ALL_DESTINATIONS: list[type[BackupDestination]] = [
    GoogleDriveDestination,
    OneDriveDestination,
    SftpDestination,
    SmbDestination,
    LocalFolderDestination,
]


def destination_class(dest_type: str) -> type[BackupDestination] | None:
    """Look up a destination class by its type string."""
    return next((d for d in ALL_DESTINATIONS if d.destination_type == dest_type), None)


def is_enabled(dest: type[BackupDestination], config: dict, env: dict[str, str]) -> bool:
    """The ``<TYPE>_ENABLED`` env var wins; otherwise fall back to services.json."""
    raw = env.get(dest.enabled_key(), "").strip()
    if raw:
        return env_flag(raw)
    return bool(config.get("destinations", {}).get(dest.destination_type, {}).get("enabled"))
