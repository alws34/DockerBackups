"""Tests for the Snipe-IT worker."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest
import requests

from app.core.context import BackupContext, BackupError
from app.workers import snipeit
from app.workers.snipeit import SnipeItWorker


def _ctx(tmp_path: Path, api_key: str = "k") -> BackupContext:
    return BackupContext(
        backup_root=tmp_path / "backups",
        log_root=tmp_path / "logs",
        state_root=tmp_path / "state",
        retention_days=30,
        env={"SNIPEIT_URL": "http://snipe/", "SNIPEIT_API_KEY": api_key},
    )


def _resp(url: str, data: object, status: int = 200) -> requests.Response:
    r = requests.Response()
    r.status_code, r.url, r.encoding = status, url, "utf-8"
    r._content = json.dumps(data).encode()
    return r


def test_all_endpoints_unreachable_raises(tmp_path: Path) -> None:
    worker = SnipeItWorker({"name": "snipeit", "type": "snipeit", "options": {}})
    with patch("app.workers.snipeit.requests.get", side_effect=requests.ConnectionError("dead")):
        ctx = _ctx(tmp_path)
        with pytest.raises(BackupError, match="All Snipe-IT endpoints failed"):
            worker.run(ctx)


def test_pages_through_endpoints_and_skips_forbidden_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(snipeit, "_PAGE_SIZE", 2)
    hardware = [{"id": i} for i in range(5)]
    offsets = []

    def route(method: str, url: str, **kw: object) -> requests.Response:
        assert kw["headers"]["Authorization"] == "Bearer k"  # "Bearer " prefix not doubled
        endpoint = url.removeprefix("http://snipe/api/v1/")
        offset, limit = kw["params"]["offset"], kw["params"]["limit"]
        if endpoint == "hardware":
            offsets.append(offset)
            return _resp(url, {"total": 5, "rows": hardware[offset : offset + limit]})
        if endpoint == "users":
            return _resp(url, {"status": "error"}, status=403)
        if endpoint == "fields":
            return _resp(url, [{"id": "f"}])  # some endpoints answer with a bare list
        return _resp(url, {"total": 0, "rows": []})

    with patch("requests.Session.request", side_effect=route):
        result = SnipeItWorker({"name": "assets", "type": "snipeit"}).run(
            _ctx(tmp_path, "Bearer k")
        )

    assert offsets == [0, 2, 4]
    assert result.message.startswith("6 records exported")
    [archive] = result.output_files
    assert archive.name.startswith("assets_")
    with tarfile.open(archive) as tar:
        files = {Path(m.name).name: json.load(tar.extractfile(m)) for m in tar if m.isfile()}
    assert files["hardware.json"] == hardware
    assert files["users.json"] == []
    assert files["fields.json"] == [{"id": "f"}]


def test_rejected_api_key_stops_the_backup(tmp_path: Path) -> None:
    with patch(
        "requests.Session.request",
        side_effect=lambda method, url, **kw: _resp(url, {"status": "error"}, status=401),
    ):
        worker = SnipeItWorker({"name": "snipeit", "type": "snipeit"})
        ctx = _ctx(tmp_path)
        with pytest.raises(BackupError, match="SNIPEIT_API_KEY is invalid or expired"):
            worker.run(ctx)
