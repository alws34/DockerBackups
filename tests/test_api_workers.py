"""Tests for the API-export workers' pagination and archive output."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.core.context import BackupContext, BackupError
from app.workers.immich import ImmichWorker
from app.workers.karakeep import KarakeepWorker
from app.workers.spoolman import SpoolmanWorker


def _ctx(tmp_path: Path, env: dict) -> BackupContext:
    return BackupContext(
        backup_root=tmp_path / "backups",
        log_root=tmp_path / "logs",
        state_root=tmp_path / "state",
        retention_days=30,
        env=env,
    )


def _resp(data: object) -> MagicMock:
    r = MagicMock()
    r.json.return_value = data
    return r


def _archive(result) -> dict:
    with tarfile.open(result.output_files[0]) as tar:
        return {
            Path(m.name).stem: json.load(tar.extractfile(m)) for m in tar.getmembers() if m.isfile()
        }


def test_karakeep_follows_cursors(tmp_path: Path) -> None:
    def route(method: str, url: str, **kw: object) -> MagicMock:
        cursor = kw.get("params", {}).get("cursor")
        if url.endswith("/lists/L1/bookmarks"):
            return _resp({"bookmarks": [{"id": "b1"}], "nextCursor": None})
        if url.endswith("/bookmarks"):
            if cursor is None:
                return _resp({"bookmarks": [{"id": "b1"}], "nextCursor": "c2"})
            return _resp({"bookmarks": [{"id": "b2"}], "nextCursor": None})
        if url.endswith("/lists"):
            return _resp({"lists": [{"id": "L1"}]})
        if url.endswith("/tags"):
            return _resp({"tags": [{"id": "t"}], "nextCursor": None})
        return _resp({"highlights": [], "nextCursor": None})

    with patch("requests.Session.request", side_effect=route):
        result = KarakeepWorker({"name": "karakeep", "type": "karakeep"}).run(
            _ctx(tmp_path, {"KARAKEEP_URL": "http://k", "KARAKEEP_API_KEY": "x"})
        )
    files = _archive(result)
    assert [b["id"] for b in files["bookmarks"]] == ["b1", "b2"]
    assert files["list_members"] == {"L1": ["b1"]}


def test_immich_pages_assets_and_people(tmp_path: Path) -> None:
    def route(method: str, url: str, **kw: object) -> MagicMock:
        if url.endswith("/search/metadata"):
            body = kw["json"]
            if "albumIds" in body:
                return _resp({"assets": {"items": [{"id": "a1"}], "nextPage": None}})
            if body["page"] == 1:
                return _resp({"assets": {"items": [{"id": "a1"}], "nextPage": "2"}})
            return _resp({"assets": {"items": [{"id": "a2"}], "nextPage": None}})
        if url.endswith("/people"):
            page = kw["params"]["page"]
            return _resp({"people": [{"id": f"p{page}"}], "hasNextPage": page == "1"})
        if url.endswith("/albums"):
            return _resp([{"id": "AL"}])
        return _resp([])

    with patch("requests.Session.request", side_effect=route):
        result = ImmichWorker({"name": "immich", "type": "immich"}).run(
            _ctx(tmp_path, {"IMMICH_URL": "http://i", "IMMICH_API_KEY": "x"})
        )
    files = _archive(result)
    assert [a["id"] for a in files["assets"]] == ["a1", "a2"]
    assert [p["id"] for p in files["people"]] == ["p1", "p2"]
    assert files["album_assets"] == {"AL": ["a1"]}


def test_endpoint_failure_fails_backup(tmp_path: Path) -> None:
    import requests

    with patch("requests.Session.request", side_effect=requests.ConnectionError("down")):
        with pytest.raises(BackupError, match="failed"):
            SpoolmanWorker({"name": "spoolman", "type": "spoolman"}).run(
                _ctx(tmp_path, {"SPOOLMAN_URL": "http://s"})
            )


def test_missing_env_fails(tmp_path: Path) -> None:
    with pytest.raises(BackupError, match="SPOOLMAN_URL"):
        SpoolmanWorker({"name": "spoolman", "type": "spoolman"}).run(_ctx(tmp_path, {}))
