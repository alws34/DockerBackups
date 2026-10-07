"""Tests for the KitchenOwl worker: per-household export, partial failures, errors."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest
import requests

from app.core.context import BackupContext, BackupError
from app.workers.kitchenowl import KitchenOwlWorker

ENV = {"KITCHENOWL_URL": "http://owl/", "KITCHENOWL_TOKEN": "t0k"}


def _ctx(tmp_path: Path, env: dict) -> BackupContext:
    return BackupContext(
        backup_root=tmp_path / "backups",
        log_root=tmp_path / "logs",
        state_root=tmp_path / "state",
        retention_days=30,
        env=env,
    )


def _resp(url: str, data: object, status: int = 200) -> requests.Response:
    r = requests.Response()
    r.status_code, r.url, r.encoding = status, url, "utf-8"
    r._content = json.dumps(data).encode()
    return r


def _worker() -> KitchenOwlWorker:
    return KitchenOwlWorker({"name": "kitchen", "type": "kitchenowl"})


def _read(archive: Path) -> dict[str, object]:
    """``{path inside the archive's top folder: parsed JSON}``."""
    with tarfile.open(archive) as tar:
        return {
            "/".join(Path(m.name).parts[1:]): json.load(tar.extractfile(m))
            for m in tar.getmembers()
            if m.isfile()
        }


def _run(tmp_path: Path, households: object, failing: str = "") -> tuple[Path, dict]:
    def route(method: str, url: str, **kw: object) -> requests.Response:
        assert kw["headers"]["Authorization"] == "Bearer t0k"
        path = url.removeprefix("http://owl")
        if path == "/api/household":
            return _resp(url, households)
        if path == failing:
            return _resp(url, {"msg": "boom"}, status=500)
        return _resp(url, [path])

    with patch("requests.Session.request", side_effect=route):
        result = _worker().run(_ctx(tmp_path, ENV))
    [archive] = result.output_files
    return archive, _read(archive)


def test_exports_every_household(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    # Both id shapes the API has used, plus an entry without one (skipped).
    households = [{"id": 1}, {"household": {"id": 2}}, {"name": "no id"}]
    archive, files = _run(tmp_path, households, failing="/api/household/2/item")

    assert archive.name.startswith("kitchen_")
    assert archive.stat().st_mode & 0o777 == 0o600
    assert files["households.json"] == households
    assert files["household_1/recipes.json"] == ["/api/household/1/recipe"]
    assert files["household_1/items.json"] == ["/api/household/1/item"]
    assert files["household_1/shoppinglists.json"] == ["/api/household/1/shoppinglist"]
    # One failing section only costs that section.
    assert files["household_2/recipes.json"] == ["/api/household/2/recipe"]
    assert "household_2/items.json" not in files
    assert "household_2/shoppinglists.json" in files
    assert "could not fetch items for household 2" in caplog.text
    assert not any(name.startswith("household_None") for name in files)


def test_single_household_object(tmp_path: Path) -> None:
    _, files = _run(tmp_path, {"id": 7})
    assert files["households.json"] == [{"id": 7}]
    assert files["household_7/recipes.json"] == ["/api/household/7/recipe"]


def test_rejected_token_fails_the_backup(tmp_path: Path) -> None:
    with patch(
        "requests.Session.request",
        side_effect=lambda method, url, **kw: _resp(url, {"msg": "Unauthorized"}, status=401),
    ):
        with pytest.raises(BackupError, match="Failed to fetch households: 401"):
            _worker().run(_ctx(tmp_path, ENV))


@pytest.mark.parametrize("missing", ["KITCHENOWL_URL", "KITCHENOWL_TOKEN"])
def test_missing_setting(tmp_path: Path, missing: str) -> None:
    with pytest.raises(BackupError, match=f"{missing} is not set"):
        _worker().run(_ctx(tmp_path, {**ENV, missing: ""}))
