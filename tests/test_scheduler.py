import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock
from datetime import datetime
from app.core.scheduler import BackupScheduler
from app.core.registry import WorkerRegistry
from app.core.context import BackupResult, BackupError


@pytest.fixture
def config_file(tmp_path):
    config = {
        "schedule": {"daily_at": "03:30", "run_on_start": False},
        "retention": {"keep_days": 7},
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
def mock_result():
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
def registry(mock_result):
    reg = MagicMock(spec=WorkerRegistry)
    worker = MagicMock()
    worker.run.return_value = mock_result
    reg.create.return_value = worker
    return reg


def test_load_config(config_file, registry):
    scheduler = BackupScheduler(str(config_file), registry)
    scheduler.load_config()
    assert scheduler.get_config()["retention"]["keep_days"] == 7


@pytest.mark.asyncio
async def test_run_service_calls_worker(config_file, registry, mock_result, tmp_path, monkeypatch):
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
async def test_disabled_services_skipped(config_file, registry, tmp_path, monkeypatch):
    monkeypatch.setenv("BACKUP_ROOT", str(tmp_path / "backups"))
    monkeypatch.setenv("LOG_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("STATE_ROOT", str(tmp_path / "state"))
    scheduler = BackupScheduler(str(config_file), registry)
    scheduler.load_config()
    await scheduler._run_all_services()
    # Only svc1 enabled, svc2 disabled
    assert registry.create.call_count == 1


@pytest.mark.asyncio
async def test_state_persisted_after_success(config_file, registry, tmp_path, monkeypatch):
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


def test_is_running_default_false(config_file, registry):
    scheduler = BackupScheduler(str(config_file), registry)
    assert scheduler.is_running("svc1") is False
