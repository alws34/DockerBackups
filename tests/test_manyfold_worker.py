"""Tests for the Manyfold worker (HTTP mocked)."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from app.core.context import BackupContext, BackupError
from app.workers.manyfold import ManyfoldWorker

_ENV = {"MANYFOLD_URL": "http://m/", "MANYFOLD_CLIENT_ID": "id", "MANYFOLD_CLIENT_SECRET": "sec"}


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


def _route(method: str, url: str, **kw: object) -> MagicMock:
    if url == "http://m/oauth/token":
        return _resp({"access_token": "tok", "token_type": "Bearer"})
    path = url.removeprefix("http://m")
    if path == "/models":
        page = kw["params"]["page"]
        # @id uses the server's public hostname, which may differ from MANYFOLD_URL.
        member = [{"@id": f"https://public.example/models/m{page}", "name": f"M{page}"}]
        view = {"next": "/models?page=2"} if page == "1" else {}
        return _resp({"member": member, "view": view})
    if path in ("/collections", "/creators"):
        return _resp({"member": [{"@id": f"{path}/x1", "name": "X"}], "view": {}})
    kind, item_id = path.strip("/").split("/")
    return _resp({"@id": url, "name": item_id, "keywords": ["tag"]})


def test_pages_lists_and_fetches_details(tmp_path: Path) -> None:
    with patch("requests.Session.request", side_effect=_route) as req:
        result = ManyfoldWorker({"name": "manyfold", "type": "manyfold"}).run(_ctx(tmp_path, _ENV))
    files = _archive(result)
    assert [m["name"] for m in files["models"]] == ["m1", "m2"]
    assert [c["name"] for c in files["collections"]] == ["x1"]
    assert [c["name"] for c in files["creators"]] == ["x1"]

    calls = req.call_args_list
    token_call = calls[0]
    assert token_call.args == ("POST", "http://m/oauth/token")
    assert token_call.kwargs["data"]["grant_type"] == "client_credentials"
    assert token_call.kwargs["data"]["scope"] == "read"
    assert {c.args[0] for c in calls[1:]} == {"GET"}
    assert all(c.kwargs["timeout"] for c in calls)
    assert result.output_files[0].name.startswith("manyfold_")


def test_rejected_client_fails_with_hint(tmp_path: Path) -> None:
    resp = MagicMock()
    resp.raise_for_status.side_effect = requests.HTTPError("401 Client Error: Unauthorized")
    with patch("requests.Session.request", return_value=resp):
        worker = ManyfoldWorker({"name": "manyfold", "type": "manyfold"})
        ctx = _ctx(tmp_path, _ENV)
        with pytest.raises(BackupError, match="client ID/secret"):
            worker.run(ctx)


def test_missing_secret_fails(tmp_path: Path) -> None:
    env = {k: v for k, v in _ENV.items() if k != "MANYFOLD_CLIENT_SECRET"}
    worker = ManyfoldWorker({"name": "manyfold", "type": "manyfold"})
    ctx = _ctx(tmp_path, env)
    with pytest.raises(BackupError, match="MANYFOLD_CLIENT_SECRET"):
        worker.run(ctx)
