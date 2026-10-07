"""Upload backups to a Windows/Samba/NAS share over SMB2/3."""

from __future__ import annotations

import errno
import shutil
from pathlib import Path
from typing import ClassVar

import smbclient
from smbprotocol.exceptions import SMBAuthenticationError, SMBException

from app.core.context import BackupError
from app.destinations.base import BackupDestination, env_flag, require
from app.workers.base import EnvVarSpec

SERVER_ENV = "SMB_SERVER"
PORT_ENV = "SMB_PORT"
SHARE_ENV = "SMB_SHARE"
PATH_ENV = "SMB_PATH"
USER_ENV = "SMB_USERNAME"
PASSWORD_ENV = "SMB_PASSWORD"  # noqa: S105 - env var name
ENCRYPT_ENV = "SMB_ENCRYPT"


class SmbDestination(BackupDestination):
    """Upload each backup into ``\\\\server\\share\\<path>\\<service>\\``."""

    destination_type: ClassVar[str] = "smb"
    display_name: ClassVar[str] = "SMB share"
    description: ClassVar[str] = (
        "Upload to a Windows, Samba or NAS share (Synology, QNAP, TrueNAS, Unraid). "
        "Talks SMB2/3 directly; nothing needs to be mounted."
    )
    env_var_specs: ClassVar[list[EnvVarSpec]] = [
        EnvVarSpec(SERVER_ENV, "Server", "Hostname or IP of the file server.", False, True),
        EnvVarSpec(SHARE_ENV, "Share", "Share name, e.g. backups.", False, True),
        EnvVarSpec(
            PATH_ENV, "Folder in share", "Optional sub-folder, e.g. homelab-takeout.", False, False
        ),
        EnvVarSpec(USER_ENV, "Username", "Account with write access to the share.", False, True),
        EnvVarSpec(PASSWORD_ENV, "Password", "Password for that account.", True, True),
        EnvVarSpec(PORT_ENV, "Port", "Defaults to 445.", False, False, advanced=True),
        EnvVarSpec(
            ENCRYPT_ENV,
            "Encrypt traffic",
            "SMB3 encryption, on unless set to 'false' (only for old servers that lack it).",
            False,
            False,
            advanced=True,
        ),
    ]

    def __init__(
        self,
        server: str,
        port: int,
        share: str,
        path: str,
        username: str,
        password: str,
        encrypt: bool,
    ) -> None:
        self.server = server
        self.port = port
        self.username = username
        self.password = password
        self.encrypt = encrypt
        self.share_root = f"\\\\{server}\\{share}"
        self.subdirs = [p for p in path.replace("/", "\\").split("\\") if p]
        self.root = "\\".join([self.share_root, *self.subdirs])
        self._registered = False

    @classmethod
    def from_env(cls, env: dict[str, str]) -> SmbDestination:
        port_raw = env.get(PORT_ENV, "").strip() or "445"
        if not port_raw.isdigit():
            raise BackupError(f"{PORT_ENV} must be a number, got '{port_raw}'.")
        encrypt_raw = env.get(ENCRYPT_ENV, "").strip()
        return cls(
            server=require(env, SERVER_ENV, "SMB server"),
            port=int(port_raw),
            share=require(env, SHARE_ENV, "SMB share").strip("\\/"),
            path=env.get(PATH_ENV, ""),
            username=require(env, USER_ENV, "SMB username"),
            password=require(env, PASSWORD_ENV, "SMB password"),
            encrypt=env_flag(encrypt_raw) if encrypt_raw else True,
        )

    def _connect(self) -> None:
        if self._registered:
            return
        try:
            smbclient.register_session(
                self.server,
                username=self.username,
                password=self.password,
                port=self.port,
                encrypt=self.encrypt,
                connection_timeout=30,
            )
        except SMBAuthenticationError as e:
            raise BackupError(f"SMB login to {self.server} as {self.username} failed.") from e
        except (SMBException, OSError, ValueError) as e:
            if "LOGON_FAILURE" in str(e):
                raise BackupError(
                    f"SMB login to {self.server} as {self.username} failed: wrong user or password."
                ) from e
            raise BackupError(f"SMB connection to {self.server}:{self.port} failed: {e}") from e
        self._registered = True
        # Probe the share up front: a missing share fails fast here, whereas smbclient's
        # makedirs would fall into a DFS referral lookup that some servers never answer.
        try:
            smbclient.listdir(self.share_root, port=self.port)
        except OSError as e:
            raise BackupError(
                f"Share '{self.share_root}' not found or not accessible as {self.username}."
            ) from e

    def _mkdirs(self, *extra: str) -> str:
        """Create folders below the share one level at a time; never touch the share root."""
        path = self.share_root
        for part in [*self.subdirs, *extra]:
            path = f"{path}\\{part}"
            try:
                smbclient.mkdir(path, port=self.port)
            except OSError as e:  # smbprotocol raises SMBOSError, not FileExistsError
                if e.errno != errno.EEXIST:
                    raise
        return path

    def _folder(self, service_name: str) -> str:
        return f"{self.root}\\{service_name}"

    def put(self, file_path: Path, service_name: str) -> None:
        self._connect()
        folder = self._mkdirs(service_name)
        tmp = f"{folder}\\.{file_path.name}.partial"
        with (
            file_path.open("rb") as src,
            smbclient.open_file(tmp, mode="wb", port=self.port) as dst,
        ):
            shutil.copyfileobj(src, dst)
        smbclient.replace(tmp, f"{folder}\\{file_path.name}", port=self.port)

    def list_names(self, service_name: str) -> list[str]:
        self._connect()
        try:
            return smbclient.listdir(self._folder(service_name), port=self.port)
        except FileNotFoundError:
            return []

    def remove(self, service_name: str, name: str) -> None:
        self._connect()
        smbclient.remove(f"{self._folder(service_name)}\\{name}", port=self.port)

    def check(self) -> str:
        self._connect()
        self._mkdirs()
        return f"Connected to {self.root} as {self.username}."

    def close(self) -> None:
        if self._registered:
            smbclient.delete_session(self.server, port=self.port)
            self._registered = False
