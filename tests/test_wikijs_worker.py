"""Tests for the Wiki.js GraphQL export worker."""

from __future__ import annotations

import tarfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.core.context import BackupContext, BackupError
from app.workers.wikijs import WikiJsWorker

SERVICE_CONFIG = {
    "name": "wikijs",
    "type": "wikijs",
    "options": {
        "wikijs_url_env": "WIKIJS_URL",
        "api_token_env": "WIKIJS_API_TOKEN",
    },
}

ENV = {
    "WIKIJS_URL": "https://wiki.example.com",
    "WIKIJS_API_TOKEN": "test-token-abc",
}

MOCK_PAGES = [
    {
        "id": 1,
        "title": "Home",
        "path": "en/home",
        "updatedAt": "2024-01-01",
        "isPublished": True,
    }
]
MOCK_PAGE_CONTENT = {
    "id": 1,
    "title": "Home",
    "path": "en/home",
    "content": "# Home\n\nWelcome.",
    "contentType": "markdown",
    "updatedAt": "2024-01-01",
}


@pytest.fixture
def context(tmp_path: Path) -> BackupContext:
    """Return a context pointing at temporary paths with valid Wiki.js env vars."""
    return BackupContext(
        backup_root=tmp_path / "backups",
        log_root=tmp_path / "logs",
        state_root=tmp_path / "state",
        retention_days=30,
        env=ENV.copy(),
    )


def test_worker_type() -> None:
    """The worker should report its registered type string."""
    assert WikiJsWorker.worker_type == "wikijs"


def test_missing_url_raises(tmp_path: Path) -> None:
    """A missing Wiki.js URL should raise BackupError."""
    ctx = BackupContext(
        backup_root=tmp_path / "backups",
        log_root=tmp_path / "logs",
        state_root=tmp_path / "state",
        retention_days=30,
        env={},
    )
    worker = WikiJsWorker(SERVICE_CONFIG)
    with pytest.raises(BackupError):
        worker.run(ctx)


def _make_list_response() -> MagicMock:
    """Build a mock HTTP response for the page-list GraphQL query."""
    mock = MagicMock()
    mock.raise_for_status = MagicMock()
    mock.json.return_value = {"data": {"pages": {"list": MOCK_PAGES}}}
    return mock


def _make_page_response() -> MagicMock:
    """Build a mock HTTP response for the single-page GraphQL query."""
    mock = MagicMock()
    mock.raise_for_status = MagicMock()
    mock.json.return_value = {"data": {"pages": {"single": MOCK_PAGE_CONTENT}}}
    return mock


def test_run_creates_tar_gz(context: BackupContext) -> None:
    """A successful run should produce a tar.gz containing the exported page."""
    worker = WikiJsWorker(SERVICE_CONFIG)
    with patch(
        "app.workers.wikijs.requests.post",
        side_effect=[_make_list_response(), _make_page_response()],
    ):
        result = worker.run(context)

    assert result.success is True
    assert "1 pages" in result.message
    assert len(result.output_files) == 1
    output_file = result.output_files[0]
    assert output_file.exists()
    assert output_file.name.endswith(".tar.gz")
    output_file.chmod(0o600)
    with tarfile.open(output_file, "r:gz") as tar:
        names = tar.getnames()
        assert any("home.md" in n for n in names)


def test_graphql_error_raises(context: BackupContext) -> None:
    """A GraphQL error payload should raise BackupError."""
    worker = WikiJsWorker(SERVICE_CONFIG)
    mock = MagicMock()
    mock.raise_for_status = MagicMock()
    mock.json.return_value = {"errors": [{"message": "Unauthorized"}]}
    with patch("app.workers.wikijs.requests.post", return_value=mock):
        with pytest.raises(BackupError, match="GraphQL error"):
            worker.run(context)


def test_env_var_specs_defined() -> None:
    """Both Wiki.js env vars should be declared, with the token marked secret."""
    specs = WikiJsWorker.env_var_specs
    keys = {s.key for s in specs}
    assert "WIKIJS_URL" in keys
    assert "WIKIJS_API_TOKEN" in keys
    token_spec = next(s for s in specs if s.key == "WIKIJS_API_TOKEN")
    assert token_spec.secret is True
