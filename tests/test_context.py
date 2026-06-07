import os
import pytest
from datetime import datetime
from pathlib import Path
from app.core.context import BackupContext, BackupResult, BackupError


def test_backup_context_from_environment(monkeypatch):
    monkeypatch.setenv("BACKUP_ROOT", "/tmp/backups")
    monkeypatch.setenv("LOG_ROOT", "/tmp/logs")
    monkeypatch.setenv("STATE_ROOT", "/tmp/state")
    ctx = BackupContext.from_environment(retention_days=7)
    assert ctx.backup_root == Path("/tmp/backups")
    assert ctx.log_root == Path("/tmp/logs")
    assert ctx.state_root == Path("/tmp/state")
    assert ctx.retention_days == 7
    assert "BACKUP_ROOT" in ctx.env


def test_backup_context_defaults(monkeypatch):
    monkeypatch.delenv("BACKUP_ROOT", raising=False)
    monkeypatch.delenv("LOG_ROOT", raising=False)
    monkeypatch.delenv("STATE_ROOT", raising=False)
    ctx = BackupContext.from_environment(retention_days=30)
    assert ctx.backup_root == Path("/backups")
    assert ctx.log_root == Path("/logs")
    assert ctx.state_root == Path("/state")


def test_backup_result_fields():
    now = datetime.now()
    result = BackupResult(
        service_name="test",
        worker_type="test_worker",
        success=True,
        message="ok",
        output_files=[Path("/backups/test.json")],
        started_at=now,
        finished_at=now,
    )
    assert result.success is True
    assert result.service_name == "test"


def test_backup_error_is_exception():
    err = BackupError("something went wrong")
    assert isinstance(err, Exception)
    assert str(err) == "something went wrong"
