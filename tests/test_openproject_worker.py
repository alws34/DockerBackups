"""Tests for the OpenProject worker (API mocked, no network)."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from app.core.context import BackupContext, BackupError
from app.workers.openproject import OpenProjectWorker

ENV = {"OPENPROJECT_URL": "http://op/sub", "OPENPROJECT_API_TOKEN": "tok"}


def _ctx(tmp_path: Path) -> BackupContext:
    return BackupContext(
        backup_root=tmp_path / "backups",
        log_root=tmp_path / "logs",
        state_root=tmp_path / "state",
        retention_days=30,
        env=ENV,
    )


def _resp(data: object) -> MagicMock:
    r = MagicMock()
    r.json.return_value = data
    return r


def _coll(elements: list, total: int | None = None) -> MagicMock:
    total = len(elements) if total is None else total
    return _resp({"total": total, "count": len(elements), "_embedded": {"elements": elements}})


def _archive(result) -> dict:
    with tarfile.open(result.output_files[0]) as tar:
        return {
            Path(m.name).stem: json.load(tar.extractfile(m)) for m in tar.getmembers() if m.isfile()
        }


def test_exports_everything_and_pages_by_offset(tmp_path: Path) -> None:
    calls: list = []
    wps = [
        {"id": i, "_links": {"attachments": {"href": f"/sub/api/v3/work_packages/{i}/attachments"}}}
        for i in (1, 2, 3)
    ]

    def route(self, method: str, url: str, **kw: object) -> MagicMock:
        calls.append((url, kw["params"], self.auth))
        page = int(kw["params"]["offset"])
        if url.endswith("/api/v3/work_packages"):
            # Server capped the page size at 2: three items arrive on two pages.
            return _coll(wps[(page - 1) * 2 : page * 2], total=3)
        if url.endswith("/api/v3/projects"):
            return _coll(
                [{"id": 7, "_links": {"categories": {"href": "/sub/api/v3/projects/7/categories"}}}]
            )
        if url.endswith("/projects/7/categories"):
            return _coll([{"id": 70, "name": "Backend"}])
        if url.endswith("/work_packages/2/attachments"):
            return _coll([{"id": 200, "fileName": "spec.pdf"}])
        if url.endswith("/attachments"):
            return _coll([])
        return _coll([{"id": 1, "_links": {"self": {"href": url}}}])

    with patch("requests.Session.request", autospec=True, side_effect=route):
        result = OpenProjectWorker({"name": "openproject", "type": "openproject"}).run(
            _ctx(tmp_path)
        )

    files = _archive(result)
    assert [w["id"] for w in files["work_packages"]] == [1, 2, 3]
    assert files["categories"] == {"7": [{"id": 70, "name": "Backend"}]}
    assert files["attachments"]["2"] == [{"id": 200, "fileName": "spec.pdf"}]
    assert files["statuses"][0]["_links"]["self"]["href"].endswith("/api/v3/statuses")
    wp_calls = [c for c in calls if c[0] == "http://op/sub/api/v3/work_packages"]
    assert [c[1]["offset"] for c in wp_calls] == ["1", "2"]
    assert all(c[1]["filters"] == "[]" for c in wp_calls)
    assert "http://op/sub/api/v3/work_packages/2/attachments" in [c[0] for c in calls]
    assert all(c[2] == ("apikey", "tok") for c in calls)
    assert result.output_files[0].name.startswith("openproject_")


def test_bad_token_gives_actionable_error(tmp_path: Path) -> None:
    resp = MagicMock()
    resp.raise_for_status.side_effect = requests.HTTPError("401 Client Error: Unauthorized")

    with patch("requests.Session.request", return_value=resp):
        worker = OpenProjectWorker({"name": "openproject", "type": "openproject"})
        ctx = _ctx(tmp_path)
        with pytest.raises(BackupError, match="OPENPROJECT_API_TOKEN"):
            worker.run(ctx)


def test_sections_the_token_cannot_see_are_skipped(tmp_path: Path) -> None:
    forbidden = MagicMock()
    forbidden.raise_for_status.side_effect = requests.HTTPError("403 Client Error: Forbidden")

    def route(method: str, url: str, **kw: object) -> MagicMock:
        if url.endswith(("/time_entries", "/queries")):
            return forbidden
        return _coll([])

    with patch("requests.Session.request", side_effect=route):
        result = OpenProjectWorker({"name": "openproject", "type": "openproject"}).run(
            _ctx(tmp_path)
        )
    files = _archive(result)
    assert files["time_entries"] == []
    assert files["queries"] == []
    assert set(files["skipped"]) == {"time_entries", "queries"}
