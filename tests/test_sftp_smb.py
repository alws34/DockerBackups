"""SFTP and SMB uploads with paramiko / smbclient mocked: atomic writes, errors, retention."""

import errno
import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import paramiko
import pytest
from smbprotocol.exceptions import SMBAuthenticationError, SMBException

from app.core.context import BackupError
from app.destinations import smb
from app.destinations.sftp import SftpDestination, fingerprint
from app.destinations.smb import SmbDestination

# ── SFTP ─────────────────────────────────────────────────────────────────────

SERVER_KEY = paramiko.ECDSAKey.generate()


def _sftp_dest(**extra: str) -> SftpDestination:
    env = {
        "SFTP_HOST": "nas.lan",
        "SFTP_USERNAME": "backup",
        "SFTP_PASSWORD": "pw",
        "SFTP_PATH": "/volume1/backups/",
        "SFTP_HOST_FINGERPRINT": fingerprint(SERVER_KEY),
        **extra,
    }
    return SftpDestination.from_env(env)


@pytest.fixture
def server():
    """A trusted server: returns (transport, sftp client) mocks."""
    transport = MagicMock()
    transport.get_remote_server_key.return_value = SERVER_KEY
    client = MagicMock()
    existing = {"/volume1"}

    def stat(path):
        if path not in existing:
            raise FileNotFoundError(path)

    client.stat.side_effect = stat
    client.mkdir.side_effect = existing.add
    with (
        patch.object(paramiko, "Transport", return_value=transport),
        patch.object(paramiko.SFTPClient, "from_transport", return_value=client),
    ):
        yield transport, client


def test_sftp_put_is_atomic_and_creates_folders(server, tmp_path: Path):
    transport, client = server
    src = tmp_path / "wikijs_20261001_033000.tar.gz"
    src.write_text("x")
    dest = _sftp_dest()
    dest.put(src, "wikijs")
    transport.auth_password.assert_called_once_with("backup", "pw")
    assert [c.args[0] for c in client.mkdir.call_args_list] == [
        "/volume1/backups",
        "/volume1/backups/wikijs",
    ]
    tmp = "/volume1/backups/wikijs/.wikijs_20261001_033000.tar.gz.partial"
    client.put.assert_called_once_with(str(src), tmp)
    client.posix_rename.assert_called_once_with(
        tmp, "/volume1/backups/wikijs/wikijs_20261001_033000.tar.gz"
    )
    dest.close()
    client.close.assert_called_once()
    transport.close.assert_called_once()


def test_sftp_rename_fallback_without_posix_extension(server, tmp_path: Path):
    _, client = server
    client.posix_rename.side_effect = OSError("unsupported")
    src = tmp_path / "n8n_20261001_000000.json"
    src.write_text("{}")
    _sftp_dest().put(src, "n8n")
    final = "/volume1/backups/n8n/n8n_20261001_000000.json"
    client.remove.assert_called_once_with(final)
    client.rename.assert_called_once_with(
        "/volume1/backups/n8n/.n8n_20261001_000000.json.partial", final
    )


def test_sftp_list_and_remove(server):
    _, client = server
    client.listdir.side_effect = FileNotFoundError()
    dest = _sftp_dest()
    assert dest.list_names("n8n") == []
    dest.remove("n8n", "n8n_1.json")
    client.remove.assert_called_once_with("/volume1/backups/n8n/n8n_1.json")
    assert "nas.lan" in dest.check()


def test_sftp_login_and_connection_failures_close_the_transport(server):
    transport, _ = server
    transport.auth_password.side_effect = paramiko.AuthenticationException()
    dest = _sftp_dest()
    with pytest.raises(BackupError, match="login to nas.lan as backup failed"):
        dest.check()
    transport.close.assert_called_once()

    dest = _sftp_dest()
    with patch.object(paramiko, "Transport", side_effect=OSError("refused")):
        with pytest.raises(BackupError, match="connection to nas.lan:22 failed: refused"):
            dest.check()


def test_sftp_key_login_uses_key_not_password(server):
    transport, _ = server
    with patch.object(paramiko.PKey, "from_path", return_value="pkey") as from_path:
        _sftp_dest(SFTP_KEY_FILE="/state/destinations/sftp_id").check()
    from_path.assert_called_once_with("/state/destinations/sftp_id")  # plain key: no passphrase
    transport.auth_publickey.assert_called_once_with("backup", "pkey")
    transport.auth_password.assert_not_called()


def test_sftp_port_must_be_numeric():
    with pytest.raises(BackupError, match="must be a number"):
        _sftp_dest(SFTP_PORT="22; rm")


# ── SMB ──────────────────────────────────────────────────────────────────────


def _smb_dest(**extra: str) -> SmbDestination:
    env = {
        "SMB_SERVER": "nas",
        "SMB_SHARE": "\\backups\\",
        "SMB_PATH": "homelab/takeout",
        "SMB_USERNAME": "u",
        "SMB_PASSWORD": "p",
        **extra,
    }
    return SmbDestination.from_env(env)


@pytest.fixture
def smbclient():
    client = MagicMock()
    written = io.BytesIO()
    written.close = lambda: None  # keep the bytes readable after the with-block
    client.open_file.return_value = written
    client.written = written
    with patch.object(smb, "smbclient", client):
        yield client


def test_smb_put_writes_partial_then_replaces(smbclient, tmp_path: Path):
    smbclient.mkdir.side_effect = OSError(errno.EEXIST, "exists")
    src = tmp_path / "wikijs_20261001_033000.tar.gz"
    src.write_bytes(b"archive")
    dest = _smb_dest()
    dest.put(src, "wikijs")
    assert smbclient.register_session.call_args.kwargs["encrypt"] is True
    # The share root itself is never created, only folders below it.
    assert [c.args[0] for c in smbclient.mkdir.call_args_list] == [
        "\\\\nas\\backups\\homelab",
        "\\\\nas\\backups\\homelab\\takeout",
        "\\\\nas\\backups\\homelab\\takeout\\wikijs",
    ]
    folder = "\\\\nas\\backups\\homelab\\takeout\\wikijs"
    smbclient.open_file.assert_called_once_with(
        f"{folder}\\.wikijs_20261001_033000.tar.gz.partial", mode="wb", port=445
    )
    assert smbclient.written.getvalue() == b"archive"
    smbclient.replace.assert_called_once_with(
        f"{folder}\\.wikijs_20261001_033000.tar.gz.partial",
        f"{folder}\\wikijs_20261001_033000.tar.gz",
        port=445,
    )
    dest.close()
    smbclient.delete_session.assert_called_once_with("nas", port=445)


def test_smb_mkdir_errors_other_than_exists_propagate(smbclient):
    smbclient.mkdir.side_effect = OSError(errno.EACCES, "denied")
    dest = _smb_dest()
    with pytest.raises(OSError, match="denied"):
        dest.check()


def test_smb_list_and_remove(smbclient):
    smbclient.listdir.side_effect = [["share-probe"], FileNotFoundError()]
    dest = _smb_dest()
    assert dest.list_names("n8n") == []
    dest.remove("n8n", "n8n_1.json")
    smbclient.remove.assert_called_once_with(
        "\\\\nas\\backups\\homelab\\takeout\\n8n\\n8n_1.json", port=445
    )
    smbclient.register_session.assert_called_once()  # session reused


def test_smb_connection_errors(smbclient):
    smbclient.register_session.side_effect = SMBAuthenticationError("bad")
    dest = _smb_dest()
    with pytest.raises(BackupError, match="login to nas as u failed"):
        dest.check()
    smbclient.register_session.side_effect = SMBException("STATUS_LOGON_FAILURE")
    dest = _smb_dest()
    with pytest.raises(BackupError, match="wrong user or password"):
        dest.check()
    smbclient.register_session.side_effect = OSError("timed out")
    dest = _smb_dest()
    with pytest.raises(BackupError, match="connection to nas:445 failed"):
        dest.check()
    smbclient.register_session.side_effect = None
    smbclient.listdir.side_effect = OSError("no share")
    dest = _smb_dest()
    with pytest.raises(BackupError, match="not found or not accessible"):
        dest.check()


def test_smb_settings():
    assert _smb_dest(SMB_ENCRYPT="false").encrypt is False
    assert _smb_dest(SMB_ENCRYPT="").encrypt is True
    with pytest.raises(BackupError, match="must be a number"):
        _smb_dest(SMB_PORT="x")
    with pytest.raises(BackupError, match="SMB password"):
        _smb_dest(SMB_PASSWORD="")
