import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from datetime import datetime
from app.core.context import BackupContext, BackupError
from app.workers.vaultwarden_encrypted_json import VaultwardenEncryptedJsonWorker


SERVICE_CONFIG = {
    "name": "vaultwarden",
    "type": "vaultwarden_encrypted_json",
    "options": {
        "vaultwarden_url_env": "VAULTWARDEN_URL",
        "client_id_env": "BW_CLIENTID",
        "client_secret_env": "BW_CLIENTSECRET",
        "master_password_env": "BW_PASSWORD",
        "export_password_env": "VAULTWARDEN_EXPORT_PASSWORD",
    },
}

ENV = {
    "BACKUP_ROOT": "/tmp/backups",
    "LOG_ROOT": "/tmp/logs",
    "STATE_ROOT": "/tmp/state",
    "VAULTWARDEN_URL": "https://vault.example.com",
    "BW_CLIENTID": "user.abc123",
    "BW_CLIENTSECRET": "secret123",
    "BW_PASSWORD": "masterpass",
    "VAULTWARDEN_EXPORT_PASSWORD": "exportpass",
}


def test_worker_type():
    worker = VaultwardenEncryptedJsonWorker(SERVICE_CONFIG)
    assert worker.worker_type == "vaultwarden_encrypted_json"


def test_env_var_specs_defined():
    specs = VaultwardenEncryptedJsonWorker.env_var_specs
    keys = {s.key for s in specs}
    assert "VAULTWARDEN_URL" in keys
    assert "BW_CLIENTID" in keys
    assert "BW_CLIENTSECRET" in keys
    assert "BW_PASSWORD" in keys
    assert "VAULTWARDEN_EXPORT_PASSWORD" in keys


def test_missing_env_raises(tmp_path):
    ctx = BackupContext(
        backup_root=tmp_path / "backups",
        log_root=tmp_path / "logs",
        state_root=tmp_path / "state",
        retention_days=30,
        env={},
    )
    worker = VaultwardenEncryptedJsonWorker(SERVICE_CONFIG)
    with patch("shutil.which", return_value="/usr/bin/bw"):
        with pytest.raises(BackupError, match="VAULTWARDEN_URL"):
            worker.run(ctx)


def test_missing_binary_raises(tmp_path):
    ctx = BackupContext(
        backup_root=tmp_path / "backups",
        log_root=tmp_path / "logs",
        state_root=tmp_path / "state",
        retention_days=30,
        env=ENV.copy(),
    )
    worker = VaultwardenEncryptedJsonWorker(SERVICE_CONFIG)
    with patch("shutil.which", return_value=None):
        with pytest.raises(BackupError, match="bw"):
            worker.run(ctx)


def test_secrets_not_in_env_var_labels():
    specs = VaultwardenEncryptedJsonWorker.env_var_specs
    secret_specs = [s for s in specs if s.secret]
    assert len(secret_specs) == 3  # clientsecret, password, export password
    for s in secret_specs:
        assert s.key in {"BW_CLIENTSECRET", "BW_PASSWORD", "VAULTWARDEN_EXPORT_PASSWORD"}
