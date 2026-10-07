"""Tests for the Bar Assistant worker: per-bar export, skipped endpoints, errors."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest
import requests

from app.core.context import BackupContext, BackupError
from app.workers.bar_assistant import BarAssistantWorker

ENV = {"BAR_ASSISTANT_URL": "http://bar/", "BAR_ASSISTANT_API_KEY": " k3y "}
BARS = [{"id": 1, "slug": "home"}, {"id": 2, "slug": None}]


def _ctx(tmp_path: Path, env: dict) -> BackupContext:
    return BackupContext(
        backup_root=tmp_path / "backups",
        log_root=tmp_path / "logs",
        state_root=tmp_path / "state",
        retention_days=30,
        env=env,
    )


def _resp(url: str, data: object = None, status: int = 200, raw: bytes | None = None):
    r = requests.Response()
    r.status_code, r.url, r.encoding = status, url, "utf-8"
    r._content = raw if raw is not None else json.dumps(data).encode()
    return r


def _worker() -> BarAssistantWorker:
    return BarAssistantWorker({"name": "cocktails", "type": "bar_assistant"})


def _read(archive: Path) -> dict[str, object]:
    """``{path inside the archive's top folder: parsed JSON}``."""
    with tarfile.open(archive) as tar:
        return {
            "/".join(Path(m.name).parts[1:]): json.load(tar.extractfile(m))
            for m in tar.getmembers()
            if m.isfile()
        }


def test_exports_one_folder_per_bar(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    calls = []

    def route(method: str, url: str, **kw: object) -> requests.Response:
        headers = kw["headers"]
        assert headers["Authorization"] == "Bearer k3y"
        endpoint = url.removeprefix("http://bar/api/")
        if endpoint == "bars":
            return _resp(url, {"data": BARS})
        bar_id = headers["Bar-Assistant-Bar-Id"]
        calls.append((endpoint, bar_id, kw["params"]))
        if endpoint == "collections":
            return _resp(url, {"message": "Forbidden"}, status=403)
        if endpoint == "tags":
            return _resp(url, {"data": {"not": "a list"}})
        return _resp(url, {"data": [{"bar": bar_id, "of": endpoint}]})

    with patch("requests.Session.request", side_effect=route):
        result = _worker().run(_ctx(tmp_path, ENV))

    [archive] = result.output_files
    assert result.success
    assert archive.name.startswith("cocktails_")
    assert archive.stat().st_mode & 0o777 == 0o600
    # 2 bars x 5 list endpoints (tags isn't a list, collections is forbidden)
    assert result.message.startswith("10 records exported")
    assert {params["per_page"] for _, _, params in calls} == {1000}

    files = _read(archive)
    assert files["bars.json"] == BARS
    assert files["bar_home/cocktails.json"] == [{"bar": "1", "of": "cocktails"}]
    assert files["bar_2/ingredients.json"] == [{"bar": "2", "of": "ingredients"}]
    assert files["bar_2/cocktail_methods.json"] == [{"bar": "2", "of": "cocktail-methods"}]
    assert files["bar_home/tags.json"] == {"not": "a list"}
    assert "bar_home/collections.json" not in files
    assert "skipping collections" in caplog.text


@pytest.mark.parametrize(
    "bars_body",
    [{"data": []}, [], {"message": "Unexpected shape"}],
    ids=["empty-data", "empty-list", "not-a-list"],
)
def test_no_bars_is_a_clear_error(tmp_path: Path, bars_body: object) -> None:
    with patch(
        "requests.Session.request", side_effect=lambda method, url, **kw: _resp(url, bars_body)
    ):
        worker = _worker()
        ctx = _ctx(tmp_path, ENV)
        with pytest.raises(BackupError, match="No bars found"):
            worker.run(ctx)


@pytest.mark.parametrize(
    ("response", "message"),
    [
        ({"status": 401}, "BAR_ASSISTANT_API_KEY is invalid or expired"),
        ({"status": 500}, "Failed to fetch bars"),
        ({"raw": b"<html>login</html>"}, "Non-JSON response"),
    ],
)
def test_bars_request_errors(tmp_path: Path, response: dict, message: str) -> None:
    with patch(
        "requests.Session.request",
        side_effect=lambda method, url, **kw: _resp(url, {}, **response),
    ):
        worker = _worker()
        ctx = _ctx(tmp_path, ENV)
        with pytest.raises(BackupError, match=message):
            worker.run(ctx)


def test_unreachable_server(tmp_path: Path) -> None:
    with patch("requests.Session.request", side_effect=requests.ConnectionError("refused")):
        worker = _worker()
        ctx = _ctx(tmp_path, ENV)
        with pytest.raises(BackupError, match="Failed to fetch bars: refused"):
            worker.run(ctx)


@pytest.mark.parametrize("missing", ["BAR_ASSISTANT_URL", "BAR_ASSISTANT_API_KEY"])
def test_missing_setting(tmp_path: Path, missing: str) -> None:
    worker = _worker()
    ctx = _ctx(tmp_path, {**ENV, missing: ""})
    with pytest.raises(BackupError, match=f"{missing} is not set"):
        worker.run(ctx)


def test_follows_every_page(tmp_path: Path) -> None:
    # A bar with more records than one page holds: page 2 must not be dropped.
    def route(method: str, url: str, **kw: object) -> requests.Response:
        endpoint = url.removeprefix("http://bar/api/")
        if endpoint == "bars":
            return _resp(url, {"data": BARS[:1]})
        if endpoint != "cocktails":
            return _resp(url, {"data": []})
        page = kw["params"].get("page", 1)
        return _resp(url, {"data": [{"id": page}], "meta": {"last_page": 3}})

    with patch("requests.Session.request", side_effect=route):
        result = _worker().run(_ctx(tmp_path, ENV))

    assert _read(result.output_files[0])["bar_home/cocktails.json"] == [
        {"id": 1},
        {"id": 2},
        {"id": 3},
    ]
