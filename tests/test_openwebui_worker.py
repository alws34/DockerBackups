"""Tests for the Open WebUI worker with the REST API mocked."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest
import requests

from app.core.context import BackupContext, BackupError
from app.workers.openwebui import OpenWebUIWorker

ENV = {"OPENWEBUI_URL": "http://owui/", "OPENWEBUI_API_KEY": "sk-secret"}
CHATS = [{"id": "c1", "chat": {"messages": [{"content": "hi"}]}}, {"id": "c2", "chat": {}}]


def _ctx(tmp_path: Path) -> BackupContext:
    return BackupContext(
        backup_root=tmp_path / "backups",
        log_root=tmp_path / "logs",
        state_root=tmp_path / "state",
        retention_days=30,
        env=ENV,
    )


def _resp(data: object, status: int = 200, text: str | None = None) -> requests.Response:
    r = requests.Response()
    r.status_code = status
    r._content = (text if text is not None else json.dumps(data)).encode()
    r.url = "http://owui"
    return r


def _api(role: str, chats_text: str | None = None):
    """Fake Open WebUI where admin-only routes answer 401 unless role is admin."""
    ndjson = "".join(json.dumps(c) + "\n" for c in CHATS)
    admin_only = ("/functions/export", "/configs/export", "/users/all")
    routes = {
        "/auths/": {"id": "u1", "role": role, "token": "sk-secret"},
        "/chats/all/tags": [{"name": "work"}],
        "/folders/": [{"id": "f1"}],
        "/folders/f1": {"id": "f1", "data": {"system_prompt": "be brief"}},
        "/notes/": [{"id": "n1"}],
        "/notes/n1": {"id": "n1", "data": {"content": {"md": "full"}}},
        "/memories/": [{"id": "m1"}],
        "/users/user/settings": {"ui": {}},
        "/models/export": [{"id": "preset"}],
        "/prompts/": [{"command": "/x"}],
        "/tools/export": [{"id": "t", "content": "code"}],
        "/skills/export": [],
        "/groups/": [],
        "/functions/export": [{"id": "fn", "content": "code"}],
        "/configs/export": {"ui": {}},
        "/users/all": {"users": [{"id": "u1"}], "total": 1},
    }

    def route(method: str, url: str, **kw: object) -> requests.Response:
        assert method == "GET"
        path = url.removeprefix("http://owui/api/v1")
        page = (kw.get("params") or {}).get("page")
        if path == "/chats/all":
            return _resp(None, text=chats_text if chats_text is not None else ndjson)
        if path in admin_only and role != "admin":
            return _resp({"detail": "access prohibited"}, status=401)
        if path == "/knowledge/":
            items = [{"id": "k1"}] if page == 1 else [{"id": "k2"}]
            return _resp({"items": items, "total": 2})
        if path.startswith("/knowledge/"):
            return _resp({"items": [{"id": "file", "data": {"content": "x"}}], "total": 1})
        return _resp(routes[path])

    return route


def _archive(result) -> dict:
    with tarfile.open(result.output_files[0]) as tar:
        return {
            Path(m.name).stem: json.load(tar.extractfile(m)) for m in tar.getmembers() if m.isfile()
        }


def test_admin_key_exports_everything(tmp_path: Path) -> None:
    with patch("requests.Session.request", side_effect=_api("admin")):
        result = OpenWebUIWorker({"name": "openwebui", "type": "openwebui"}).run(_ctx(tmp_path))
    files = _archive(result)
    assert result.output_files[0].name.startswith("openwebui_")
    assert files["chats"] == CHATS
    assert "token" not in files["user"]
    assert "sk-secret" not in json.dumps(files)
    assert files["folders"][0]["data"]["system_prompt"] == "be brief"
    assert files["notes"][0]["data"]["content"]["md"] == "full"
    assert [kb["id"] for kb in files["knowledge"]] == ["k1", "k2"]
    assert files["knowledge"][0]["files"] == [{"id": "file"}]
    assert files["functions"][0]["content"] == "code"
    assert files["users"]["users"] == [{"id": "u1"}]
    assert files["skipped"] == {}


def test_user_key_skips_admin_only_endpoints(tmp_path: Path) -> None:
    with patch("requests.Session.request", side_effect=_api("user")):
        result = OpenWebUIWorker({"name": "openwebui", "type": "openwebui"}).run(_ctx(tmp_path))
    files = _archive(result)
    assert files["chats"] == CHATS
    assert {"functions", "config", "users"}.isdisjoint(files)
    assert files["skipped"]["config"].startswith("HTTP 401")
    assert "admin_only" in files["skipped"]


def test_older_versions_return_a_json_array_of_chats(tmp_path: Path) -> None:
    with patch("requests.Session.request", side_effect=_api("admin", json.dumps(CHATS))):
        result = OpenWebUIWorker({"name": "openwebui", "type": "openwebui"}).run(_ctx(tmp_path))
    assert _archive(result)["chats"] == CHATS


@pytest.mark.parametrize(("status", "match"), [(401, "rejected"), (403, "Enable API Keys")])
def test_unusable_key_fails_with_hint(tmp_path: Path, status: int, match: str) -> None:
    with patch("requests.Session.request", return_value=_resp({}, status=status)):
        worker = OpenWebUIWorker({"name": "openwebui", "type": "openwebui"})
        ctx = _ctx(tmp_path)
        with pytest.raises(BackupError, match=match):
            worker.run(ctx)


def test_server_error_fails_backup(tmp_path: Path) -> None:
    api = _api("admin")

    def route(method: str, url: str, **kw: object) -> requests.Response:
        if url.endswith("/prompts/"):
            return _resp({}, status=500)
        return api(method, url, **kw)

    with patch("requests.Session.request", side_effect=route):
        worker = OpenWebUIWorker({"name": "openwebui", "type": "openwebui"})
        ctx = _ctx(tmp_path)
        with pytest.raises(BackupError, match="prompts"):
            worker.run(ctx)
