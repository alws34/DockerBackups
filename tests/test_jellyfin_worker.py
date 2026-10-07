"""Tests for the Jellyfin worker: paging, merged watch state, containers, redaction."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from app.core.context import BackupContext, BackupError
from app.workers import jellyfin
from app.workers.jellyfin import JellyfinWorker

ENV = {"JELLYFIN_URL": "http://jf/", "JELLYFIN_API_KEY": "k3y"}


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


def _page(*ids: str, type_: str = "Movie") -> MagicMock:
    return _resp({"Items": [{"Id": i, "Type": type_} for i in ids], "TotalRecordCount": 99})


def _route(method: str, url: str, **kw: object) -> MagicMock:
    assert method == "GET"
    params = kw.get("params", {})
    if url.endswith("/Users"):
        return _resp([{"Id": "u1", "Name": "alice"}])
    if url.endswith("/Playlists/P1/Items"):
        assert params["userId"] == "u1"
        return _page("m2")
    if url.endswith("/Playlists/P1"):
        return _resp({"OpenAccess": False, "Shares": [], "ItemIds": ["m2"]})
    if url.endswith("/Items"):
        if params.get("includeItemTypes"):
            if params["startIndex"] != "0":
                return _page()
            return _resp(
                {"Items": [{"Id": "P1", "Type": "Playlist"}, {"Id": "B1", "Type": "BoxSet"}]}
            )
        if params.get("parentId") == "B1":
            return _page("m3")
        if params.get("filters") == "IsPlayed":
            # Two full pages of size 2, then a short one.
            return {"0": _page("m1", "m2"), "2": _page("m3", "m4"), "4": _page("m5")}[
                params["startIndex"]
            ]
        if params.get("filters") == "IsFavorite":
            return _page("m1")  # also played: must not be duplicated
        return _page()
    if url.endswith("/System/Configuration/network"):
        return _resp({"PublicHttpPort": 8096, "CertificatePassword": "hunter2"})
    if url.endswith("/Plugins"):
        return _resp([{"Name": "TMDb"}])
    if url.endswith("/Library/VirtualFolders"):
        return _resp([{"Name": "Movies"}])
    return _resp({})


def test_jellyfin_exports_everything(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jellyfin, "_PAGE_SIZE", 2)
    with patch("requests.Session.request", side_effect=_route) as req:
        result = JellyfinWorker({"name": "jellyfin", "type": "jellyfin"}).run(_ctx(tmp_path, ENV))

    assert result.output_files[0].name.startswith("jellyfin_")
    with tarfile.open(result.output_files[0]) as tar:
        raw = {Path(m.name).stem: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}
    files = {k: json.loads(v) for k, v in raw.items()}

    assert [i["Id"] for i in files["watch_state"]["alice"]] == ["m1", "m2", "m3", "m4", "m5"]
    [playlist] = files["playlists"]
    assert [i["Id"] for i in playlist["items"]] == ["m2"]
    assert playlist["sharing"]["ItemIds"] == ["m2"]
    [collection] = files["collections"]
    assert collection["collection"]["Id"] == "B1"
    assert [i["Id"] for i in collection["items"]] == ["m3"]
    assert files["libraries"] == [{"Name": "Movies"}]
    assert files["plugins"] == [{"Name": "TMDb"}]
    assert files["server_configuration"]["network"] == {"PublicHttpPort": 8096}
    assert not any(b"hunter2" in v or b"k3y" in v for v in raw.values())

    first = req.call_args_list[0]
    assert first.args[1] == "http://jf/Users"
    assert all(c.kwargs["timeout"] for c in req.call_args_list)


def test_jellyfin_bad_key_fails_backup(tmp_path: Path) -> None:
    def unauthorized(method: str, url: str, **kw: object) -> MagicMock:
        r = MagicMock()
        r.raise_for_status.side_effect = requests.HTTPError("401 Client Error: Unauthorized")
        return r

    with patch("requests.Session.request", side_effect=unauthorized):
        worker = JellyfinWorker({"name": "jellyfin", "type": "jellyfin"})
        ctx = _ctx(tmp_path, ENV)
        with pytest.raises(BackupError, match="401"):
            worker.run(ctx)


def test_jellyfin_sends_mediabrowser_token(tmp_path: Path) -> None:
    seen = {}

    def capture(self: requests.Session, method: str, url: str, **kw: object) -> MagicMock:
        seen.update(self.headers)
        raise requests.ConnectionError("down")

    with patch.object(requests.Session, "request", autospec=True, side_effect=capture):
        worker = JellyfinWorker({"name": "jellyfin", "type": "jellyfin"})
        ctx = _ctx(tmp_path, ENV)
        with pytest.raises(BackupError, match="failed"):
            worker.run(ctx)
    assert seen["Authorization"] == 'MediaBrowser Token="k3y"'


def test_jellyfin_missing_key_fails(tmp_path: Path) -> None:
    worker = JellyfinWorker({"name": "jellyfin", "type": "jellyfin"})
    ctx = _ctx(tmp_path, {"JELLYFIN_URL": "http://jf"})
    with pytest.raises(BackupError, match="JELLYFIN_API_KEY"):
        worker.run(ctx)
