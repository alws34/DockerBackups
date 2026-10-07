"""Tests for the Plex worker: paging, TV episodes, secret prefs and failures."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from app.core.context import BackupContext, BackupError
from app.workers import plex
from app.workers.plex import PlexWorker

ENV = {"PLEX_URL": "http://plex:32400/", "PLEX_TOKEN": "tok"}


def _ctx(tmp_path: Path) -> BackupContext:
    return BackupContext(
        backup_root=tmp_path / "backups",
        log_root=tmp_path / "logs",
        state_root=tmp_path / "state",
        retention_days=30,
        env=ENV,
    )


def _resp(container: dict) -> MagicMock:
    r = MagicMock()
    r.json.return_value = {"MediaContainer": container}
    return r


def _archive(result) -> dict:
    with tarfile.open(result.output_files[0]) as tar:
        return {
            Path(m.name).stem: json.load(tar.extractfile(m)) for m in tar.getmembers() if m.isfile()
        }


def _route(method: str, url: str, **kw: object) -> MagicMock:
    path = url.removeprefix("http://plex:32400")
    params = kw.get("params") or {}
    start = int((kw.get("headers") or {}).get("X-Plex-Container-Start", 0))
    if path == "/":
        return _resp({"friendlyName": "srv", "version": "1.41"})
    if path == "/library/sections":
        return _resp({"Directory": [{"key": "1", "type": "movie"}, {"key": "2", "type": "show"}]})
    if path.endswith("/prefs") and path.startswith("/library"):
        return _resp({"Setting": [{"id": "enableCinemaTrailers", "value": True}]})
    if path == "/library/sections/1/all":
        if params.get("type") == 18:
            return _resp({"Metadata": [{"ratingKey": "c1"}]})
        # Two pages of movies (page size patched to 2 below).
        movies = [{"ratingKey": f"m{i}", "viewCount": i} for i in range(3)]
        return _resp({"totalSize": 3, "Metadata": movies[start : start + 2]})
    if path == "/library/sections/2/all":
        by_type = {2: [{"ratingKey": "s1", "type": "show"}], 4: [{"ratingKey": "e1"}]}
        return _resp({"Metadata": by_type.get(params.get("type"), [])})
    if path == "/library/collections/c1/children":
        return _resp({"Metadata": [{"ratingKey": "m0"}]})
    if path == "/playlists":
        return _resp({"Metadata": [{"ratingKey": "p1"}]})
    if path == "/playlists/p1/items":
        return _resp({"Metadata": [{"ratingKey": "m1"}]})
    if path == "/status/sessions/history/all":
        return _resp({"totalSize": 1, "Metadata": [{"ratingKey": "m1", "accountID": 1}]})
    if path == "/accounts":
        return _resp({"Account": [{"id": 1, "name": "owner"}]})
    if path == "/:/prefs":
        return _resp(
            {
                "Setting": [
                    {"id": "FriendlyName", "value": "srv"},
                    {"id": "PlexOnlineToken", "value": "leak"},
                    {"id": "customCertificateKey", "value": "leak"},
                ]
            }
        )
    raise AssertionError(f"unexpected {path}")


def test_plex_export(tmp_path: Path) -> None:
    with (
        patch.object(plex, "_PAGE_SIZE", 2),
        patch("requests.Session.request", side_effect=_route) as req,
    ):
        result = PlexWorker({"name": "plex", "type": "plex"}).run(_ctx(tmp_path))

    files = _archive(result)
    assert [m["ratingKey"] for m in files["items"]["1"]] == ["m0", "m1", "m2"]
    assert [m["ratingKey"] for m in files["items"]["2"]] == ["s1", "e1"]
    assert files["collection_items"] == {"c1": [{"ratingKey": "m0"}]}
    assert files["playlist_items"] == {"p1": [{"ratingKey": "m1"}]}
    assert files["history"][0]["accountID"] == 1
    assert [p["id"] for p in files["prefs"]] == ["FriendlyName"]
    assert "leak" not in json.dumps(files)
    assert result.output_files[0].name.startswith("plex_")
    assert all(c.kwargs["timeout"] for c in req.call_args_list)


def test_bad_token_says_so(tmp_path: Path) -> None:
    err = requests.HTTPError("401 Client Error: Unauthorized")
    resp = MagicMock()
    resp.raise_for_status.side_effect = err
    with patch("requests.Session.request", return_value=resp):
        worker = PlexWorker({"name": "plex", "type": "plex"})
        ctx = _ctx(tmp_path)
        with pytest.raises(BackupError, match="PLEX_TOKEN"):
            worker.run(ctx)


def test_unreachable_server_fails(tmp_path: Path) -> None:
    with patch("requests.Session.request", side_effect=requests.ConnectionError("down")):
        worker = PlexWorker({"name": "plex", "type": "plex"})
        ctx = _ctx(tmp_path)
        with pytest.raises(BackupError, match="failed"):
            worker.run(ctx)
