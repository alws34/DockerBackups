"""Upload backups to any SSH server over SFTP."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import posixpath
from pathlib import Path
from typing import ClassVar

import paramiko

from app.core.context import BackupError
from app.destinations.base import BackupDestination, UnknownHostKeyError, require
from app.workers.base import EnvVarSpec

HOST_ENV = "SFTP_HOST"
PORT_ENV = "SFTP_PORT"
USER_ENV = "SFTP_USERNAME"
PASSWORD_ENV = "SFTP_PASSWORD"  # noqa: S105 - env var name
KEY_FILE_ENV = "SFTP_KEY_FILE"
PATH_ENV = "SFTP_PATH"
FINGERPRINT_ENV = "SFTP_HOST_FINGERPRINT"

_TIMEOUT = 30


def fingerprint(key: paramiko.PKey) -> str:
    """OpenSSH-style SHA256 fingerprint, as shown by ``ssh-keygen -lf``."""
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


class SftpDestination(BackupDestination):
    """Upload each backup into ``<path>/<service>/`` on an SSH server."""

    destination_type: ClassVar[str] = "sftp"
    display_name: ClassVar[str] = "SFTP"
    description: ClassVar[str] = (
        "Upload to any SSH server (NAS, VPS, another box). Use a password or an SSH key. "
        "The server's host key is pinned the first time you test the connection."
    )
    env_var_specs: ClassVar[list[EnvVarSpec]] = [
        EnvVarSpec(HOST_ENV, "Host", "Hostname or IP of the SSH server.", False, True),
        EnvVarSpec(PORT_ENV, "Port", "Defaults to 22.", False, False),
        EnvVarSpec(USER_ENV, "Username", "SSH user that owns the target folder.", False, True),
        EnvVarSpec(
            PASSWORD_ENV,
            "Password",
            "Login password, or the key's passphrase when an SSH key is set.",
            True,
            False,
        ),
        EnvVarSpec(
            PATH_ENV,
            "Remote folder",
            "Absolute path on the server, e.g. /volume1/backups/homelab-takeout.",
            False,
            True,
        ),
        EnvVarSpec(
            KEY_FILE_ENV,
            "SSH key file",
            "Set automatically when you paste a private key below.",
            False,
            False,
            advanced=True,
        ),
        EnvVarSpec(
            FINGERPRINT_ENV,
            "Trusted host key",
            "Filled in when you confirm the server's fingerprint after Test connection.",
            False,
            False,
            advanced=True,
        ),
    ]

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        key_file: str,
        path: str,
        expected_fingerprint: str,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.key_file = key_file
        self.path = path
        self.expected_fingerprint = expected_fingerprint
        self._transport: paramiko.Transport | None = None
        self._sftp: paramiko.SFTPClient | None = None

    @classmethod
    def from_env(cls, env: dict[str, str]) -> SftpDestination:
        port_raw = env.get(PORT_ENV, "").strip() or "22"
        if not port_raw.isdigit():
            raise BackupError(f"{PORT_ENV} must be a number, got '{port_raw}'.")
        path = require(env, PATH_ENV, "SFTP remote folder")
        if not path.startswith("/"):
            raise BackupError(f"{PATH_ENV} must be an absolute path, got '{path}'.")
        password = env.get(PASSWORD_ENV, "")
        key_file = env.get(KEY_FILE_ENV, "").strip()
        if not password and not key_file:
            raise BackupError("Set an SFTP password or paste an SSH key.")
        return cls(
            host=require(env, HOST_ENV, "SFTP host"),
            port=int(port_raw),
            username=require(env, USER_ENV, "SFTP username"),
            password=password,
            key_file=key_file,
            path=path.rstrip("/") or "/",
            expected_fingerprint=env.get(FINGERPRINT_ENV, "").strip(),
        )

    def _client(self) -> paramiko.SFTPClient:
        if self._sftp is not None:
            return self._sftp
        try:
            # The Transport constructor already opens the TCP connection.
            transport = paramiko.Transport((self.host, self.port))
            self._transport = transport
            transport.banner_timeout = _TIMEOUT
            transport.start_client(timeout=_TIMEOUT)
            # Verify the server before sending any credentials to it.
            seen = fingerprint(transport.get_remote_server_key())
            if not self.expected_fingerprint:
                raise UnknownHostKeyError(seen)
            if not hmac.compare_digest(seen, self.expected_fingerprint):
                raise BackupError(
                    f"Host key for {self.host} changed: expected {self.expected_fingerprint}, "
                    f"got {seen}. If you rebuilt the server, test the connection again and "
                    "trust the new key; otherwise someone may be intercepting the connection."
                )
            if self.key_file:
                pkey = paramiko.PKey.from_path(self.key_file, password=self.password or None)
                transport.auth_publickey(self.username, pkey)
            else:
                transport.auth_password(self.username, self.password)
            self._sftp = paramiko.SFTPClient.from_transport(transport)
        except paramiko.AuthenticationException as e:
            self.close()
            raise BackupError(f"SFTP login to {self.host} as {self.username} failed.") from e
        except (paramiko.SSHException, OSError) as e:
            self.close()
            raise BackupError(f"SFTP connection to {self.host}:{self.port} failed: {e}") from e
        except BackupError:
            self.close()
            raise
        return self._sftp

    def _mkdirs(self, sftp: paramiko.SFTPClient, path: str) -> None:
        current = ""
        for part in path.strip("/").split("/"):
            current = f"{current}/{part}"
            try:
                sftp.stat(current)
            except FileNotFoundError:
                sftp.mkdir(current)

    def put(self, file_path: Path, service_name: str) -> None:
        sftp = self._client()
        folder = posixpath.join(self.path, service_name)
        self._mkdirs(sftp, folder)
        tmp = posixpath.join(folder, f".{file_path.name}.partial")
        final = posixpath.join(folder, file_path.name)
        sftp.put(str(file_path), tmp)
        try:
            sftp.posix_rename(tmp, final)  # OpenSSH extension: atomic, overwrites
        except OSError:
            # Plain SFTP rename can't overwrite; servers without the extension need this.
            with contextlib.suppress(OSError):
                sftp.remove(final)
            sftp.rename(tmp, final)

    def list_names(self, service_name: str) -> list[str]:
        try:
            return self._client().listdir(posixpath.join(self.path, service_name))
        except FileNotFoundError:
            return []

    def remove(self, service_name: str, name: str) -> None:
        self._client().remove(posixpath.join(self.path, service_name, name))

    def check(self) -> str:
        sftp = self._client()
        self._mkdirs(sftp, self.path)
        return f"Connected to {self.host} as {self.username}; {self.path} is ready."

    def close(self) -> None:
        if self._sftp is not None:
            self._sftp.close()
            self._sftp = None
        if self._transport is not None:
            self._transport.close()
            self._transport = None


def save_private_key(env: dict[str, str], key_text: str, dest_dir: Path) -> Path:
    """Validate a pasted private key and store it (mode 600); return its path."""
    from app.destinations.base import write_private

    text = key_text.strip() + "\n"
    if "PRIVATE KEY" not in text:
        raise BackupError("That doesn't look like a private key (expected a PEM/OpenSSH block).")
    path = dest_dir / "sftp_id"
    write_private(path, text)
    try:
        paramiko.PKey.from_path(str(path), password=env.get(PASSWORD_ENV) or None)
    except paramiko.PasswordRequiredException as e:
        path.unlink(missing_ok=True)
        raise BackupError("The key is encrypted: save its passphrase as the password first.") from e
    except (paramiko.SSHException, ValueError) as e:
        path.unlink(missing_ok=True)
        raise BackupError(f"Could not read the key: {e}") from e
    return path
