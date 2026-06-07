"""Tests for the BackupContext, BackupResult, and BackupError types."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from app.core.context import BackupContext, BackupError, BackupResult


def test_backup_context_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Environment variables should populate the corresponding context paths."""
    monkeypatch.setenv("BACKUP_ROOT", "/tmp/backups")
    monkeypatch.setenv("LOG_ROOT", "/tmp/logs")
    monkeypatch.setenv("STATE_ROOT", "/tmp/state")
    ctx = BackupContext.from_environment(retention_days=7)
    assert ctx.backup_root == Path("/tmp/backups")
    assert ctx.log_root == Path("/tmp/logs")
    assert ctx.state_root == Path("/tmp/state")
    assert ctx.retention_days == 7
    assert "BACKUP_ROOT" in ctx.env


def test_backup_context_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset environment variables should fall back to the default paths."""
    monkeypatch.delenv("BACKUP_ROOT", raising=False)
    monkeypatch.delenv("LOG_ROOT", raising=False)
    monkeypatch.delenv("STATE_ROOT", raising=False)
    ctx = BackupContext.from_environment(retention_days=30)
    assert ctx.backup_root == Path("/backups")
    assert ctx.log_root == Path("/logs")
    assert ctx.state_root == Path("/state")


def test_backup_result_fields() -> None:
    """A BackupResult should retain the fields it was constructed with."""
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


def test_backup_error_is_exception() -> None:
    """BackupError should behave like a standard exception."""
    err = BackupError("something went wrong")
    assert isinstance(err, Exception)
    assert str(err) == "something went wrong"
