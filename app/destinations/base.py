"""Shared interface and helpers for backup upload destinations."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from app.core.context import BackupError
from app.workers.base import EnvVarSpec


@dataclass
class DeviceLogin:
    """A started device-code login: the user enters ``user_code`` at ``verification_url``.

    ``wait`` blocks until the user finishes (or the code expires), stores the tokens,
    and returns a label for the connected account.
    """

    user_code: str
    verification_url: str
    expires_in: int
    wait: Callable[[], str]


class UnknownHostKeyError(BackupError):
    """Raised when a server's host key has not been trusted yet."""

    def __init__(self, fingerprint: str) -> None:
        super().__init__(f"Unknown host key {fingerprint}. Confirm it to trust this server.")
        self.fingerprint = fingerprint


class BackupDestination(ABC):
    """Somewhere finished backup files are copied to, one folder per service.

    Subclasses implement ``put``/``list_names``/``remove``; uploading plus retention
    pruning is shared in ``ship``. Settings come from env vars declared in
    ``env_var_specs`` so the GUI can render a form for them.
    """

    destination_type: ClassVar[str]
    display_name: ClassVar[str]
    description: ClassVar[str]
    env_var_specs: ClassVar[list[EnvVarSpec]]
    # "google" / "microsoft" when the GUI should offer a "Login with ..." button.
    login_provider: ClassVar[str | None] = None

    @classmethod
    def enabled_key(cls) -> str:
        """Env var that switches this destination on (``"true"``) or off."""
        return f"{cls.destination_type.upper()}_ENABLED"

    @classmethod
    def login_available(cls, env: dict[str, str]) -> bool:
        """Whether a "Login with ..." button can be offered (an OAuth client is known)."""
        return False

    @classmethod
    def login_status(cls, env: dict[str, str]) -> dict[str, object] | None:
        """``{"connected": bool, "account": str}`` for login-based destinations, else None."""
        return None

    @classmethod
    def start_login(cls, env: dict[str, str]) -> DeviceLogin:
        """Begin a device-code login. Only login-based destinations implement this."""
        raise BackupError(f"{cls.display_name} does not use a login.")

    @classmethod
    def disconnect(cls, env: dict[str, str]) -> None:
        """Revoke and forget stored tokens. Only login-based destinations implement this."""
        raise BackupError(f"{cls.display_name} does not use a login.")

    @classmethod
    @abstractmethod
    def from_env(cls, env: dict[str, str]) -> BackupDestination:
        """Build the destination from settings, raising ``BackupError`` if misconfigured."""

    @abstractmethod
    def put(self, file_path: Path, service_name: str) -> None:
        """Copy one backup file into the service's folder."""

    @abstractmethod
    def list_names(self, service_name: str) -> list[str]:
        """Return the file names currently in the service's folder."""

    @abstractmethod
    def remove(self, service_name: str, name: str) -> None:
        """Delete one file from the service's folder."""

    @abstractmethod
    def check(self) -> str:
        """Verify settings and credentials; return a short human-readable status."""

    def close(self) -> None:  # noqa: B027 - optional hook, most destinations hold no connection
        """Release any open connection."""

    def ship(self, file_path: Path, service_name: str, keep_count: int) -> list[str]:
        """Upload ``file_path``, then prune old backups; return the names pruned."""
        self.put(file_path, service_name)
        stale = names_to_prune(self.list_names(service_name), service_name, keep_count)
        for name in stale:
            self.remove(service_name, name)
        return stale


def names_to_prune(names: list[str], service_name: str, keep_count: int) -> list[str]:
    """Return the oldest backups beyond ``keep_count``.

    Only names this app produces (``<service>_<YYYYmmdd_HHMMSS>...``) are considered,
    so files a user put in the folder themselves are never deleted. The timestamp in
    the name makes lexical order chronological.
    """
    if keep_count <= 0:
        return []
    ours = sorted(n for n in names if n.startswith(f"{service_name}_"))
    return ours[: max(len(ours) - keep_count, 0)]


def env_flag(value: str | None) -> bool:
    """Interpret an env var as a boolean (``true``/``1``/``yes``/``on``)."""
    return (value or "").strip().lower() in {"true", "1", "yes", "on"}


def require(env: dict[str, str], key: str, what: str) -> str:
    """Return a non-empty setting or raise a ``BackupError`` naming it."""
    value = env.get(key, "").strip()
    if not value:
        raise BackupError(f"{what} is not set ({key}).")
    return value


def state_dir(env: dict[str, str]) -> Path:
    """Directory for destination tokens and keys (inside STATE_ROOT, never in git)."""
    return Path(env.get("STATE_ROOT", "/state")) / "destinations"


def write_private(path: Path, content: str) -> None:
    """Atomically write a secret file readable only by this user (mode 600).

    The temp file is created 0600 from the start and swapped in with ``os.replace``,
    so a crash mid-write never leaves a truncated token file behind.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(content)
    os.replace(tmp, path)
    path.chmod(0o600)
