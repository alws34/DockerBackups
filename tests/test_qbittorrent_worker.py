"""Tests for the qBittorrent worker (WebUI API mocked, no network)."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.core.context import BackupContext, BackupError
from app.workers.qbittorrent import QBittorrentWorker, strip_secrets

ENV = {
    "QBITTORRENT_URL": "http://qbt.test:8080/",
    "QBITTORRENT_USERNAME": "admin",
    "QBITTORRENT_PASSWORD": "pw",
}
PREFS = {
    "save_path": "/downloads",
    "web_ui_username": "admin",
    "web_ui_password": "SECRET-1",
    "proxy_password": "SECRET-2",
    "mail_notification_password": "SECRET-3",
    "dyndns_password": "SECRET-4",
    "web_ui_api_key": "SECRET-5",
    "bypass_local_auth": False,
    "web_ui_https_key_path": "/key.pem",
}


def _ctx(tmp_path: Path) -> BackupContext:
    return BackupContext(
        backup_root=tmp_path / "backups",
        log_root=tmp_path / "logs",
        state_root=tmp_path / "state",
        retention_days=30,
        env=ENV,
    )


def _resp(
    data: object = None, status: int = 200, text: str = "", content: bytes = b""
) -> MagicMock:
    r = MagicMock(status_code=status, text=text, content=content)
    r.json.return_value = data
    return r


def _router(export_status: int = 200, login: MagicMock | None = None):
    calls: list[tuple[str, str, dict]] = []

    def route(method: str, url: str, **kw: object) -> MagicMock:
        calls.append((method, url, kw))
        path = url.split("/api/v2", 1)[1]
        if path == "/auth/login":
            return login or _resp(text="Ok.")
        if path == "/torrents/export":
            return _resp(status=export_status, content=b"d4:infod4:name1:xee")
        return {
            "/torrents/info": _resp([{"hash": "abc", "name": "x", "category": "tv"}]),
            "/torrents/properties": _resp({"save_path": "/downloads/tv"}),
            "/torrents/trackers": _resp([{"url": "udp://t"}]),
            "/torrents/files": _resp([{"name": "x.mkv"}]),
            "/app/preferences": _resp(dict(PREFS)),
            "/app/version": _resp(text="v5.0.2\n"),
            "/app/webapiVersion": _resp(text="2.11.2"),
            "/app/buildInfo": _resp({"qt": "6.7"}),
            "/torrents/categories": _resp({"tv": {"name": "tv", "savePath": "/tv"}}),
            "/torrents/tags": _resp(["seed"]),
            "/rss/items": _resp({"Feed": {"url": "https://f"}}),
            "/rss/rules": _resp({"Rule": {"enabled": True}}),
        }.get(path, _resp(text="Ok."))

    return route, calls


def _members(result) -> dict[str, bytes]:
    with tarfile.open(result.output_files[0]) as tar:
        return {
            "/".join(Path(m.name).parts[1:]): tar.extractfile(m).read()
            for m in tar.getmembers()
            if m.isfile()
        }


def test_happy_path_exports_everything_and_logs_out(tmp_path: Path) -> None:
    route, calls = _router()
    with patch("requests.Session.request", side_effect=route):
        result = QBittorrentWorker({"name": "qbittorrent", "type": "qbittorrent"}).run(
            _ctx(tmp_path)
        )

    assert result.output_files[0].name.startswith("qbittorrent_")
    files = _members(result)
    assert files["torrents/abc.torrent"] == b"d4:infod4:name1:xee"
    details = json.loads(files["torrent_details.json"])
    assert details["abc"]["properties"]["save_path"] == "/downloads/tv"
    assert details["abc"]["trackers"] == [{"url": "udp://t"}]
    assert json.loads(files["version.json"])["app"] == "v5.0.2"
    assert "tv" in json.loads(files["categories.json"])
    assert json.loads(files["rss_rules.json"]) == {"Rule": {"enabled": True}}

    method, url, kw = calls[0]
    assert (method, url) == ("POST", "http://qbt.test:8080/api/v2/auth/login")
    assert kw["data"] == {"username": "admin", "password": "pw"}
    assert calls[-1][:2] == ("POST", "http://qbt.test:8080/api/v2/auth/logout")
    assert all("timeout" in kw for _, _, kw in calls)
    assert all(m == "GET" for m, u, _ in calls if "/auth/" not in u)


def test_csrf_headers_match_the_url(tmp_path: Path) -> None:
    seen: list[dict] = []
    route, _ = _router()

    def capture(self, method: str, url: str, **kw: object) -> MagicMock:
        seen.append(dict(self.headers))
        return route(method, url, **kw)

    with patch("requests.Session.request", autospec=True, side_effect=capture):
        QBittorrentWorker({"name": "qbittorrent", "type": "qbittorrent"}).run(_ctx(tmp_path))
    assert seen[0]["Referer"] == "http://qbt.test:8080/"
    assert seen[0]["Origin"] == "http://qbt.test:8080"


def test_secrets_are_stripped_from_preferences(tmp_path: Path) -> None:
    route, _ = _router()
    with patch("requests.Session.request", side_effect=route):
        result = QBittorrentWorker({"name": "qbittorrent", "type": "qbittorrent"}).run(
            _ctx(tmp_path)
        )
    files = _members(result)
    prefs = json.loads(files["preferences.json"])
    removed = {
        "web_ui_password",
        "proxy_password",
        "mail_notification_password",
        "dyndns_password",
        "web_ui_api_key",
    }
    assert not removed & prefs.keys()
    assert {"save_path", "bypass_local_auth", "web_ui_https_key_path"} <= prefs.keys()
    assert set(json.loads(files["notes.json"])["preferences_removed"]) == removed
    # No secret value leaks anywhere in the archive.
    blob = b"".join(files.values())
    assert b"SECRET-" not in blob
    assert b'"pw"' not in blob


def test_strip_secrets_keeps_non_secret_keys() -> None:
    kept, removed = strip_secrets({"hashing_threads": 2, "proxy_password": "x"})
    assert kept == {"hashing_threads": 2}
    assert removed == ["proxy_password"]


def test_export_404_is_skipped_with_a_note(tmp_path: Path) -> None:
    route, _ = _router(export_status=404)
    with patch("requests.Session.request", side_effect=route):
        result = QBittorrentWorker({"name": "qbittorrent", "type": "qbittorrent"}).run(
            _ctx(tmp_path)
        )
    assert result.success
    files = _members(result)
    assert not any(name.endswith(".torrent") for name in files)
    notes = json.loads(files["notes.json"])
    assert notes["torrents_not_exported"] == {"abc": "HTTP 404"}
    assert "4.5" in notes["torrent_export"]
    assert "1 skipped" in result.message


@pytest.mark.parametrize(
    ("login", "match"),
    [
        (_resp(text="Fails."), "Login refused"),
        (_resp(status=401, text="Unauthorized"), "Login refused"),
        (_resp(status=403, text="Forbidden"), "banned"),
    ],
)
def test_login_failure_raises(tmp_path: Path, login: MagicMock, match: str) -> None:
    route, calls = _router(login=login)
    with patch("requests.Session.request", side_effect=route):
        worker = QBittorrentWorker({"name": "qbittorrent", "type": "qbittorrent"})
        ctx = _ctx(tmp_path)
        with pytest.raises(BackupError, match=match):
            worker.run(ctx)
    assert len(calls) == 1


def test_missing_env_fails(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    ctx.env = {}
    worker = QBittorrentWorker({"name": "qbittorrent", "type": "qbittorrent"})
    with pytest.raises(BackupError, match="QBITTORRENT_URL"):
        worker.run(ctx)
