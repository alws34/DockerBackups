"""Tests for the Uptime Kuma worker with the Socket.IO client mocked."""

from __future__ import annotations

import json
import tarfile
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.core.context import BackupContext, BackupError
from app.workers.uptimekuma import UptimeKumaWorker, totp

_ENV = {
    "UPTIMEKUMA_URL": "http://kuma:3001/",
    "UPTIMEKUMA_USERNAME": "admin",
    "UPTIMEKUMA_PASSWORD": "pw",
}
_READ_ONLY = {
    "login",
    "getTags",
    "getSettings",
    "getMonitorMaintenance",
    "getMaintenanceStatusPage",
}

# What the server pushes after login, in the shapes it really uses (dicts keyed by id or arrays).
_PUSHES = {
    "monitorList": {"1": {"id": 1, "name": "web", "basic_auth_pass": "x"}},
    "notificationList": [{"id": 5, "name": "tg", "config": "{}"}],
    "proxyList": [],
    "dockerHostList": [{"id": 2, "name": "local"}],
    "apiKeyList": [{"id": 7, "name": "metrics"}],
    "maintenanceList": {"3": {"id": 3, "title": "patch day"}},
    "statusPageList": {"1": {"id": 1, "slug": "home", "title": "Home"}},
    "remoteBrowserList": [],
    "info": {"version": "2.0.2"},
    "heartbeatList": None,  # ignored
}


class FakeSio:
    """Stands in for socketio.Client; pushes events on handler threads like the real one."""

    def __init__(self, replies: dict, pushes: dict = _PUSHES) -> None:
        self.replies = replies
        self.pushes = pushes
        self.calls: list[tuple[str, object]] = []
        self.disconnected = False

    def __call__(self, **kw: object) -> FakeSio:
        return self

    def on(self, event: str):
        def deco(fn):
            self.handler = fn
            return fn

        return deco

    def connect(self, url: str, **kw: object) -> None:
        self.url = url

    def call(self, event: str, data: object = None, timeout: int = 60) -> dict:
        self.calls.append((event, data))
        reply = self.replies[event]
        if event == "login":
            reply = reply.pop(0)
            if reply.get("ok"):
                for name, payload in self.pushes.items():
                    threading.Thread(target=self.handler, args=(name, payload)).start()
        return reply

    def disconnect(self) -> None:
        self.disconnected = True


def _ctx(tmp_path: Path, env: dict) -> BackupContext:
    return BackupContext(
        backup_root=tmp_path / "backups",
        log_root=tmp_path / "logs",
        state_root=tmp_path / "state",
        retention_days=30,
        env=env,
    )


def _replies(*logins: dict) -> dict:
    return {
        "login": list(logins),
        "getTags": {"ok": True, "tags": [{"id": 1, "name": "prod"}]},
        "getSettings": {"ok": True, "data": {"primaryBaseURL": "https://k", "steamAPIKey": "s"}},
        "getMonitorMaintenance": {"ok": True, "monitors": [{"id": 1}]},
        "getMaintenanceStatusPage": {"ok": True, "statusPages": [{"id": 1}]},
    }


def _run(tmp_path: Path, sio: FakeSio, env: dict = _ENV):
    page = MagicMock()
    page.json.return_value = {
        "config": {"slug": "home"},
        "publicGroupList": [{"name": "Services", "monitorList": [{"id": 1}]}],
        "incident": None,
    }
    with (
        patch("app.workers.uptimekuma.socketio.Client", sio),
        patch("requests.Session.request", return_value=page) as req,
    ):
        result = UptimeKumaWorker({"name": "kuma", "type": "uptimekuma"}).run(_ctx(tmp_path, env))
    return result, req


def test_exports_everything_read_only(tmp_path: Path) -> None:
    sio = FakeSio(_replies({"ok": True, "token": "jwt"}))
    result, req = _run(tmp_path, sio)

    with tarfile.open(result.output_files[0]) as tar:
        files = {
            Path(m.name).stem: json.load(tar.extractfile(m)) for m in tar.getmembers() if m.isfile()
        }
    assert sio.url == "http://kuma:3001"
    assert {e for e, _ in sio.calls} <= _READ_ONLY
    assert sio.disconnected
    assert req.call_args.args[1] == "http://kuma:3001/api/status-page/home"
    assert files["monitors"] == [_PUSHES["monitorList"]["1"]]
    assert files["notifications"][0]["name"] == "tg"
    assert files["docker_hosts"] and files["api_keys"] and files["proxies"] == []
    assert files["remote_browsers"] == []
    assert files["maintenance"] == [
        {"id": 3, "title": "patch day", "monitors": [{"id": 1}], "status_pages": [{"id": 1}]}
    ]
    assert files["status_pages"][0]["config"]["title"] == "Home"
    assert files["status_pages"][0]["publicGroupList"][0]["name"] == "Services"
    assert files["tags"] == [{"id": 1, "name": "prod"}]
    assert files["settings"] == {"primaryBaseURL": "https://k"}  # API key stripped
    assert files["info"] == {"version": "2.0.2"}
    assert "heartbeatList" not in files


def test_v1_has_no_remote_browsers(tmp_path: Path) -> None:
    pushes = {k: v for k, v in _PUSHES.items() if k != "remoteBrowserList"}
    pushes["info"] = {"version": "1.23.17"}
    result, _ = _run(tmp_path, FakeSio(_replies({"ok": True}), pushes))
    with tarfile.open(result.output_files[0]) as tar:
        names = {Path(m.name).stem for m in tar.getmembers() if m.isfile()}
    assert "monitors" in names and "remote_browsers" not in names


def test_login_failure_raises(tmp_path: Path) -> None:
    sio = FakeSio(_replies({"ok": False, "msg": "authIncorrectCreds"}))
    with pytest.raises(BackupError, match="login failed.*authIncorrectCreds"):
        _run(tmp_path, sio)
    assert sio.disconnected
    assert [e for e, _ in sio.calls] == ["login"]


def test_2fa_without_secret_raises(tmp_path: Path) -> None:
    sio = FakeSio(_replies({"tokenRequired": True}))
    with pytest.raises(BackupError, match="UPTIMEKUMA_TOTP_SECRET"):
        _run(tmp_path, sio)


def test_2fa_sends_code_only_when_asked(tmp_path: Path) -> None:
    sio = FakeSio(_replies({"tokenRequired": True}, {"ok": True}))
    _run(tmp_path, sio, {**_ENV, "UPTIMEKUMA_TOTP_SECRET": "GEZDGNBVGY3TQOJQ"})
    logins = [d for e, d in sio.calls if e == "login"]
    assert "token" not in logins[0]
    assert len(logins[1]["token"]) == 6


def test_totp_matches_rfc6238() -> None:
    # RFC 6238 SHA-1 vector: secret "12345678901234567890", T=59 -> 94287082 (last 6 digits).
    assert totp("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", at=59) == "287082"
