"""Tests for the Snipe-IT worker."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import requests

from app.core.context import BackupContext, BackupError
from app.workers.snipeit import SnipeItWorker


def test_all_endpoints_unreachable_raises(tmp_path: Path) -> None:
    ctx = BackupContext(
        backup_root=tmp_path / "backups",
        log_root=tmp_path / "logs",
        state_root=tmp_path / "state",
        retention_days=30,
        env={"SNIPEIT_URL": "https://snipeit.example.com", "SNIPEIT_API_KEY": "k"},
    )
    worker = SnipeItWorker({"name": "snipeit", "type": "snipeit", "options": {}})
    with patch("app.workers.snipeit.requests.get", side_effect=requests.ConnectionError("dead")):
        with pytest.raises(BackupError, match="All Snipe-IT endpoints failed"):
            worker.run(ctx)
