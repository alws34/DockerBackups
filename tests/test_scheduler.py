"""Tests for the BackupScheduler config loading, running, and state handling."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.core.context import BackupResult
from app.core.env_manager import EnvManager
from app.core.registry import WorkerRegistry, create_default_registry
from app.core.scheduler import BackupScheduler


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    """Return a temporary services.json with one enabled and one disabled service."""
    config = {
        "schedule": {"daily_at": "03:30", "run_on_start": False},
        "retention": {"keep_days": 7, "remote_keep_count": 5},
        "destinations": {},
        "services": [
            {"name": "svc1", "type": "dummy", "enabled": True, "options": {}},
            {"name": "svc2", "type": "dummy", "enabled": False, "options": {}},
        ],
    }
    f = tmp_path / "services.json"
    f.write_text(json.dumps(config))
    return f


@pytest.fixture
def mock_result() -> BackupResult:
    """Return a successful BackupResult for a stub worker to emit."""
    return BackupResult(
        service_name="svc1",
        worker_type="dummy",
        success=True,
        message="ok",
        output_files=[],
        started_at=datetime.now(),
        finished_at=datetime.now(),
    )


@pytest.fixture
def registry(mock_result: BackupResult) -> MagicMock:
    """Return a mock registry whose worker returns the provided result."""
    reg = MagicMock(spec=WorkerRegistry)
    worker = MagicMock()
    worker.run.return_value = mock_result
    reg.create.return_value = worker
    return reg


def test_load_config(config_file: Path, registry: MagicMock) -> None:
    """Loading config should expose the parsed retention settings."""
    scheduler = BackupScheduler(str(config_file), registry)
    scheduler.load_config()
    assert scheduler.get_config()["retention"]["keep_days"] == 7
    assert scheduler.get_config()["retention"]["remote_keep_count"] == 5


def test_get_settings_includes_remote_keep_count(config_file: Path, registry: MagicMock) -> None:
    """get_settings should expose remote_keep_count from config."""
    scheduler = BackupScheduler(str(config_file), registry)
    scheduler.load_config()
    settings = scheduler.get_settings()
    assert settings["remote_keep_count"] == 5


def test_update_settings_persists_remote_keep_count(config_file: Path, registry: MagicMock) -> None:
    """update_settings should persist remote_keep_count to disk."""
    scheduler = BackupScheduler(str(config_file), registry)
    scheduler.load_config()
    scheduler.update_settings("04:00", 0, False, 14, 7)
    scheduler.load_config()
    assert scheduler.get_settings()["remote_keep_count"] == 7


@pytest.mark.asyncio
async def test_run_service_calls_worker(
    config_file: Path,
    registry: MagicMock,
    mock_result: BackupResult,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Running a service should return the worker's successful result."""
    monkeypatch.setenv("BACKUP_ROOT", str(tmp_path / "backups"))
    monkeypatch.setenv("LOG_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("STATE_ROOT", str(tmp_path / "state"))
    scheduler = BackupScheduler(str(config_file), registry)
    scheduler.load_config()
    service_config = {"name": "svc1", "type": "dummy", "enabled": True, "options": {}}
    result = await scheduler.run_service(service_config)
    assert result is not None
    assert result.success is True


@pytest.mark.asyncio
async def test_disabled_services_skipped(
    config_file: Path,
    registry: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only enabled services should be created during a full run."""
    monkeypatch.setenv("BACKUP_ROOT", str(tmp_path / "backups"))
    monkeypatch.setenv("LOG_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("STATE_ROOT", str(tmp_path / "state"))
    scheduler = BackupScheduler(str(config_file), registry)
    scheduler.load_config()
    await scheduler._run_all_services()
    # Only svc1 is enabled; svc2 is disabled.
    assert registry.create.call_count == 1


@pytest.mark.asyncio
async def test_state_persisted_after_success(
    config_file: Path,
    registry: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful run should write a last_result.json with success True."""
    state_dir = tmp_path / "state"
    monkeypatch.setenv("BACKUP_ROOT", str(tmp_path / "backups"))
    monkeypatch.setenv("LOG_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("STATE_ROOT", str(state_dir))
    scheduler = BackupScheduler(str(config_file), registry)
    scheduler.load_config()
    await scheduler.run_service({"name": "svc1", "type": "dummy", "enabled": True, "options": {}})
    state_file = state_dir / "svc1" / "last_result.json"
    assert state_file.exists()
    data = json.loads(state_file.read_text())
    assert data["success"] is True


def test_is_running_default_false(config_file: Path, registry: MagicMock) -> None:
    """A service that has not run should report as not running."""
    scheduler = BackupScheduler(str(config_file), registry)
    assert scheduler.is_running("svc1") is False


def test_first_start_creates_empty_config(tmp_path: Path):
    config = tmp_path / "config" / "services.json"
    env_file = tmp_path / ".env"
    env_file.touch()
    scheduler = BackupScheduler(str(config), create_default_registry(), EnvManager(env_file))
    scheduler.load_config()
    assert json.loads(config.read_text())["services"] == []


def test_first_start_after_upgrade_restores_configured_apps(tmp_path: Path):
    config = tmp_path / "services.json"
    env_file = tmp_path / ".env"
    # Snipe-IT fully set up, n8n only half: only Snipe-IT comes back.
    env_file.write_text("SNIPEIT_URL=http://s\nSNIPEIT_API_KEY=k\nN8N_URL=http://n\n")
    scheduler = BackupScheduler(str(config), create_default_registry(), EnvManager(env_file))
    scheduler.load_config()
    assert [s["name"] for s in json.loads(config.read_text())["services"]] == ["snipeit"]


def test_state_keeps_a_capped_run_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("STATE_ROOT", str(tmp_path / "state"))
    scheduler = BackupScheduler(str(tmp_path / "services.json"), create_default_registry())
    for i in range(35):
        ok = i % 2 == 0
        scheduler._write_state(
            "svc",
            {
                "success": ok,
                "finished_at": f"run-{i}",
                "uploads": {"sftp": {"ok": i % 4 == 0}},
            },
        )
    history = scheduler.get_state("svc")["history"]
    assert len(history) == 30
    assert history[-1]["finished_at"] == "run-34"
    assert history[-1] == {"finished_at": "run-34", "success": True, "delivered": False}


async def test_unsafe_service_name_never_becomes_a_path(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("STATE_ROOT", str(tmp_path / "state"))
    scheduler = BackupScheduler(str(tmp_path / "services.json"), create_default_registry())
    assert await scheduler.run_service({"name": "../../etc", "type": "n8n"}) is None
    assert scheduler.get_state("../../etc") is None
    assert not (tmp_path / "etc").exists()
