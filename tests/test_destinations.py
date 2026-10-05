"""Tests for upload destinations: pruning, local folder, logins, SFTP host keys."""

import json
import stat
from datetime import datetime
from pathlib import Path
from typing import ClassVar
from unittest.mock import MagicMock, patch

import paramiko
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.core import scheduler as scheduler_module
from app.core.context import BackupContext, BackupError, BackupResult
from app.destinations import google_drive
from app.destinations.base import (
    BackupDestination,
    UnknownHostKeyError,
    names_to_prune,
    write_private,
)
from app.destinations.local_folder import LocalFolderDestination
from app.destinations.registry import is_enabled
from app.destinations.sftp import SftpDestination, fingerprint, save_private_key

# ── pruning ──────────────────────────────────────────────────────────────────


def test_prune_keeps_newest_and_ignores_foreign_files():
    names = [
        "wikijs_20261003_033000.tar.gz",
        "wikijs_20261001_033000.tar.gz",
        "wikijs_20261002_033000.tar.gz",
        "notes-from-me.txt",  # put there by the user: never touched
        ".wikijs_20261004_033000.tar.gz.partial",  # an in-flight upload
    ]
    assert names_to_prune(names, "wikijs", 2) == ["wikijs_20261001_033000.tar.gz"]


def test_prune_disabled_or_under_limit():
    assert names_to_prune(["n8n_20261001_000000.json"], "n8n", 0) == []
    assert names_to_prune(["n8n_20261001_000000.json"], "n8n", 3) == []


def test_write_private_is_mode_600_and_leaves_no_temp(tmp_path: Path):
    target = tmp_path / "sub" / "tokens.json"
    write_private(target, "first")
    write_private(target, "second")
    assert target.read_text() == "second"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert [p.name for p in target.parent.iterdir()] == ["tokens.json"]


# ── local folder ─────────────────────────────────────────────────────────────


def test_local_folder_ships_and_prunes(tmp_path: Path):
    dest = LocalFolderDestination.from_env({"LOCAL_FOLDER_PATH": str(tmp_path / "out")})
    (tmp_path / "out" / "wikijs").mkdir(parents=True)
    (tmp_path / "out" / "wikijs" / "keep-me.txt").write_text("mine")
    for day in (1, 2, 3, 4):
        src = tmp_path / f"wikijs_2026100{day}_033000.tar.gz"
        src.write_text(str(day))
        dest.ship(src, "wikijs", keep_count=3)
    remaining = sorted(p.name for p in (tmp_path / "out" / "wikijs").iterdir())
    assert remaining == [
        "keep-me.txt",
        "wikijs_20261002_033000.tar.gz",
        "wikijs_20261003_033000.tar.gz",
        "wikijs_20261004_033000.tar.gz",
    ]
    assert dest.check().startswith("Writable")


def test_local_folder_requires_absolute_path():
    with pytest.raises(BackupError, match="absolute"):
        LocalFolderDestination.from_env({"LOCAL_FOLDER_PATH": "relative/dir"})
    with pytest.raises(BackupError, match="not set"):
        LocalFolderDestination.from_env({})


def test_enabled_env_overrides_config():
    config = {"destinations": {"local_folder": {"enabled": True}}}
    assert is_enabled(LocalFolderDestination, config, {})
    assert not is_enabled(LocalFolderDestination, config, {"LOCAL_FOLDER_ENABLED": "false"})
    assert is_enabled(LocalFolderDestination, {}, {"LOCAL_FOLDER_ENABLED": "true"})


# ── scheduler isolation ──────────────────────────────────────────────────────


class _Good(BackupDestination):
    destination_type: ClassVar[str] = "good"
    display_name: ClassVar[str] = "Good"
    description: ClassVar[str] = ""
    env_var_specs: ClassVar[list] = []
    shipped: ClassVar[list[str]] = []

    @classmethod
    def from_env(cls, env):
        return cls()

    def put(self, file_path, service_name):
        self.shipped.append(file_path.name)

    def list_names(self, service_name):
        return []

    def remove(self, service_name, name):
        pass

    def check(self):
        return "ok"


class _Broken(_Good):
    destination_type: ClassVar[str] = "broken"
    display_name: ClassVar[str] = "Broken"

    @classmethod
    def from_env(cls, env):
        raise BackupError("not connected")


def test_one_failing_destination_does_not_stop_others(tmp_path: Path):
    archive = tmp_path / "wikijs_20261005_033000.tar.gz"
    archive.write_text("x")
    result = BackupResult(
        service_name="wikijs",
        worker_type="wikijs",
        success=True,
        message="ok",
        output_files=[archive],
        started_at=datetime.now(),
        finished_at=datetime.now(),
    )
    context = BackupContext(
        backup_root=tmp_path,
        log_root=tmp_path,
        state_root=tmp_path,
        retention_days=30,
        env={"BROKEN_ENABLED": "true", "GOOD_ENABLED": "true"},
    )
    sched = scheduler_module.BackupScheduler.__new__(scheduler_module.BackupScheduler)
    sched._config = {}
    with (
        patch.object(scheduler_module, "ALL_DESTINATIONS", [_Broken, _Good]),
        patch.dict(
            scheduler_module._DESTINATION_LOCKS, {"good": MagicMock(), "broken": MagicMock()}
        ),
    ):
        uploads = sched._upload_to_destinations(result, context)
    assert uploads["broken"] == {"ok": False, "message": "not connected"}
    assert uploads["good"]["ok"] is True
    assert _Good.shipped == [archive.name]


# ── Google device login ──────────────────────────────────────────────────────


def _resp(ok: bool, body: dict) -> MagicMock:
    r = MagicMock()
    r.ok = ok
    r.json.return_value = body
    return r


def test_google_login_needs_a_client(monkeypatch):
    monkeypatch.setattr(google_drive.oauth_apps, "GOOGLE_CLIENT_ID", "")
    assert not google_drive.GoogleDriveDestination.login_available({})
    with pytest.raises(BackupError, match="no built-in Google app"):
        google_drive.GoogleDriveDestination.start_login({})


def test_google_device_login_stores_tokens(tmp_path: Path, monkeypatch):
    tokens = tmp_path / "google-tokens.json"
    monkeypatch.setenv("GOOGLE_TOKENS_FILE", str(tokens))
    monkeypatch.setattr(google_drive.time, "sleep", lambda s: None)
    env = {"GOOGLE_DEVICE_CLIENT_ID": "cid", "GOOGLE_DEVICE_CLIENT_SECRET": "csecret"}
    device = _resp(
        True,
        {
            "device_code": "dc",
            "user_code": "WDJB-MJHT",
            "verification_url": "https://www.google.com/device",
            "expires_in": 1800,
            "interval": 5,
        },
    )
    polls = [
        _resp(False, {"error": "authorization_pending"}),
        _resp(False, {"error": "slow_down"}),
        _resp(True, {"access_token": "at", "refresh_token": "rt"}),
    ]
    with (
        patch.object(google_drive.requests, "post", side_effect=[device, *polls]) as post,
        patch.object(
            google_drive.requests,
            "get",
            return_value=_resp(True, {"user": {"emailAddress": "me@example.com"}}),
        ),
    ):
        login = google_drive.GoogleDriveDestination.start_login(env)
        assert login.user_code == "WDJB-MJHT"
        assert login.wait() == "me@example.com"
    assert post.call_args_list[0].kwargs["data"]["scope"].endswith("/drive.file")
    saved = json.loads(tokens.read_text())
    assert saved["refresh_token"] == "rt" and saved["client_id"] == "cid"
    assert stat.S_IMODE(tokens.stat().st_mode) == 0o600
    status = google_drive.GoogleDriveDestination.login_status(env)
    assert status == {"connected": True, "account": "me@example.com"}


def test_google_device_login_denied(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GOOGLE_TOKENS_FILE", str(tmp_path / "t.json"))
    monkeypatch.setattr(google_drive.time, "sleep", lambda s: None)
    with patch.object(
        google_drive.requests, "post", return_value=_resp(False, {"error": "access_denied"})
    ):
        with pytest.raises(BackupError, match="denied"):
            google_drive._poll_for_tokens("cid", "s", "dc", 1, 60)
    assert not (tmp_path / "t.json").exists()


def test_google_disconnect_revokes_and_deletes(tmp_path: Path, monkeypatch):
    tokens = tmp_path / "google-tokens.json"
    tokens.write_text(json.dumps({"refresh_token": "rt"}))
    monkeypatch.setenv("GOOGLE_TOKENS_FILE", str(tokens))
    with patch.object(google_drive.requests, "post") as post:
        google_drive.GoogleDriveDestination.disconnect({})
    assert post.call_args.kwargs["data"] == {"token": "rt"}
    assert not tokens.exists()


# ── SFTP host key pinning ────────────────────────────────────────────────────


def _sftp(expected: str = "") -> SftpDestination:
    return SftpDestination.from_env(
        {
            "SFTP_HOST": "nas.lan",
            "SFTP_USERNAME": "backup",
            "SFTP_PASSWORD": "pw",
            "SFTP_PATH": "/backups",
            "SFTP_HOST_FINGERPRINT": expected,
        }
    )


def _fake_transport(server_key: paramiko.PKey) -> MagicMock:
    transport = MagicMock()
    transport.get_remote_server_key.return_value = server_key
    return transport


def test_sftp_unknown_host_key_is_reported_before_login():
    key = paramiko.ECDSAKey.generate()
    transport = _fake_transport(key)
    with patch.object(paramiko, "Transport", return_value=transport):
        with pytest.raises(UnknownHostKeyError) as err:
            _sftp().check()
    assert err.value.fingerprint == fingerprint(key)
    assert err.value.fingerprint.startswith("SHA256:")
    transport.auth_password.assert_not_called()


def test_sftp_changed_host_key_refuses_to_send_credentials():
    transport = _fake_transport(paramiko.ECDSAKey.generate())
    with patch.object(paramiko, "Transport", return_value=transport):
        with pytest.raises(BackupError, match="changed"):
            _sftp(expected="SHA256:somethingElse").check()
    transport.auth_password.assert_not_called()


def test_sftp_settings_validation():
    with pytest.raises(BackupError, match="password or paste an SSH key"):
        SftpDestination.from_env({"SFTP_HOST": "h", "SFTP_USERNAME": "u", "SFTP_PATH": "/p"})
    with pytest.raises(BackupError, match="absolute"):
        SftpDestination.from_env(
            {"SFTP_HOST": "h", "SFTP_USERNAME": "u", "SFTP_PASSWORD": "x", "SFTP_PATH": "rel"}
        )


def test_save_private_key_validates_and_is_private(tmp_path: Path):
    pem = (
        Ed25519PrivateKey.generate()
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.OpenSSH,
            serialization.NoEncryption(),
        )
        .decode()
    )
    path = save_private_key({}, pem, tmp_path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(BackupError, match="doesn't look like a private key"):
        save_private_key({}, "hello", tmp_path)


class _Hangs(_Good):
    destination_type: ClassVar[str] = "hangs"
    display_name: ClassVar[str] = "Hangs"

    def put(self, file_path, service_name):
        import time

        time.sleep(5)


def test_hung_destination_times_out_without_blocking_others(tmp_path: Path):
    archive = tmp_path / "n8n_20261005_033000.json"
    archive.write_text("{}")
    now = datetime.now()
    result = BackupResult("n8n", "n8n", True, "ok", [archive], now, now)
    context = BackupContext(
        tmp_path, tmp_path, tmp_path, 30, {"HANGS_ENABLED": "true", "GOOD_ENABLED": "true"}
    )
    sched = scheduler_module.BackupScheduler.__new__(scheduler_module.BackupScheduler)
    sched._config = {}
    import threading

    locks = {"hangs": threading.Lock(), "good": threading.Lock()}
    with (
        patch.object(scheduler_module, "ALL_DESTINATIONS", [_Hangs, _Good]),
        patch.object(scheduler_module, "_UPLOAD_TIMEOUT_SECONDS", 0.2),
        patch.dict(scheduler_module._DESTINATION_LOCKS, locks),
    ):
        uploads = sched._upload_to_destinations(result, context)
    assert uploads["hangs"]["ok"] is False and "Timed out" in uploads["hangs"]["message"]
    assert uploads["good"]["ok"] is True
