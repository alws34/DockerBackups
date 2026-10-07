"""Tests for the n8n REST API export worker."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.core.context import BackupContext, BackupError
from app.workers.n8n import N8nWorker

SERVICE_CONFIG = {"name": "n8n", "type": "n8n", "options": {}}

ENV = {
    "N8N_URL": "https://n8n.example.com",
    "N8N_API_KEY": "test-api-key",
}

MOCK_WORKFLOWS = [{"id": "1", "name": "My Workflow", "active": True}]
MOCK_TAGS = [{"id": "t1", "name": "prod"}]
MOCK_VARIABLES = [{"id": "v1", "key": "MY_VAR", "value": "hello"}]


@pytest.fixture
def context(tmp_path: Path) -> BackupContext:
    return BackupContext(
        backup_root=tmp_path / "backups",
        log_root=tmp_path / "logs",
        state_root=tmp_path / "state",
        retention_days=30,
        env=ENV.copy(),
    )


def _mock_response(data: list, next_cursor: str | None = None) -> MagicMock:
    mock = MagicMock()
    mock.raise_for_status = MagicMock()
    payload: dict = {"data": data}
    if next_cursor:
        payload["nextCursor"] = next_cursor
    mock.json.return_value = payload
    return mock


def test_worker_type() -> None:
    assert N8nWorker.worker_type == "n8n"


def test_missing_url_raises(tmp_path: Path) -> None:
    ctx = BackupContext(
        backup_root=tmp_path / "backups",
        log_root=tmp_path / "logs",
        state_root=tmp_path / "state",
        retention_days=30,
        env={"N8N_API_KEY": "key"},
    )
    worker = N8nWorker(SERVICE_CONFIG)
    with pytest.raises(BackupError, match="N8N_URL"):
        worker.run(ctx)


def test_missing_api_key_raises(tmp_path: Path) -> None:
    ctx = BackupContext(
        backup_root=tmp_path / "backups",
        log_root=tmp_path / "logs",
        state_root=tmp_path / "state",
        retention_days=30,
        env={"N8N_URL": "https://n8n.example.com"},
    )
    worker = N8nWorker(SERVICE_CONFIG)
    with pytest.raises(BackupError, match="N8N_API_KEY"):
        worker.run(ctx)


def test_run_creates_json(context: BackupContext) -> None:
    worker = N8nWorker(SERVICE_CONFIG)
    side_effects = [
        _mock_response(MOCK_WORKFLOWS),  # workflows page 1
        _mock_response(MOCK_TAGS),  # tags page 1
        _mock_response(MOCK_VARIABLES),  # variables page 1
    ]
    with patch("app.workers.n8n.requests.get", side_effect=side_effects):
        result = worker.run(context)

    assert result.success is True
    assert "1 workflows" in result.message
    assert len(result.output_files) == 1
    output_file = result.output_files[0]
    assert output_file.exists()
    assert output_file.name.startswith("n8n_")
    assert output_file.name.endswith(".json")
    assert output_file.stat().st_mode & 0o777 == 0o600

    saved = json.loads(output_file.read_text())
    assert saved["workflows"] == MOCK_WORKFLOWS
    assert saved["tags"] == MOCK_TAGS
    assert saved["variables"] == MOCK_VARIABLES


def test_pagination_collects_all_pages(context: BackupContext) -> None:
    """Two workflow pages should be merged into one list."""
    worker = N8nWorker(SERVICE_CONFIG)
    wf_page1 = _mock_response([{"id": "1", "name": "WF1"}], next_cursor="cur2")
    wf_page2 = _mock_response([{"id": "2", "name": "WF2"}])
    side_effects = [
        wf_page1,
        wf_page2,
        _mock_response(MOCK_TAGS),
        _mock_response(MOCK_VARIABLES),
    ]
    with patch("app.workers.n8n.requests.get", side_effect=side_effects):
        result = worker.run(context)

    assert result.success is True
    saved = json.loads(result.output_files[0].read_text())
    assert len(saved["workflows"]) == 2


def test_variables_endpoint_failure_skipped(context: BackupContext) -> None:
    """A missing /variables endpoint should not abort the backup."""
    import requests as req_lib

    worker = N8nWorker(SERVICE_CONFIG)
    error_resp = MagicMock()
    error_resp.raise_for_status.side_effect = req_lib.HTTPError("404 Not Found")
    side_effects = [
        _mock_response(MOCK_WORKFLOWS),
        _mock_response(MOCK_TAGS),
        error_resp,
    ]
    with patch("app.workers.n8n.requests.get", side_effect=side_effects):
        result = worker.run(context)

    assert result.success is True
    saved = json.loads(result.output_files[0].read_text())
    assert saved["variables"] == []


def test_http_error_raises(context: BackupContext) -> None:
    """An HTTP error on the workflows endpoint should raise BackupError."""
    import requests as req_lib

    worker = N8nWorker(SERVICE_CONFIG)
    err_resp = MagicMock()
    err_resp.raise_for_status.side_effect = req_lib.HTTPError("500 Server Error")
    with patch("app.workers.n8n.requests.get", return_value=err_resp):
        with pytest.raises(BackupError, match="Failed to fetch"):
            worker.run(context)


def test_env_var_specs_defined() -> None:
    specs = N8nWorker.env_var_specs
    keys = {s.key for s in specs}
    assert "N8N_URL" in keys
    assert "N8N_API_KEY" in keys
    api_key_spec = next(s for s in specs if s.key == "N8N_API_KEY")
    assert api_key_spec.secret is True
    url_spec = next(s for s in specs if s.key == "N8N_URL")
    assert url_spec.secret is False
