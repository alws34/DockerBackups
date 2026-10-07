"""Tests for the SparkyFitness worker (HTTP mocked)."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from app.core.context import BackupContext, BackupError
from app.workers.sparkyfitness import SparkyFitnessWorker


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
    params = kw.get("params") or {}
    path = url.split("/api", 1)[1]
    if path == "/identity/user":
        return _resp({"activeUserId": "u1"})
    if path == "/measurements/custom-categories":
        return _resp([{"id": "c1"}])
    if path == "/foods/foods-paginated":
        return _resp({"foods": [{"id": f"f{params['currentPage']}"}], "totalCount": 2})
    if path == "/exercises":
        return _resp({"exercises": [], "totalCount": 0})
    if path == "/workout-presets":
        return _resp({"presets": [{"id": "w1"}], "total": "1"})
    if path == "/v2/exercise-entries/history":
        page = params["page"]
        return _resp({"sessions": [{"id": f"s{page}"}], "pagination": {"hasMore": page == "1"}})
    if path.startswith("/food-entries/range/2024-"):
        return _resp([{"id": "fe2024"}])
    if path.startswith("/measurements/custom-measurements-range/c1/2025-"):
        return _resp([{"id": "cm2025"}])
    if path == "/goals/for-date":
        return _resp({params["date"]: {"calories": 2000}})
    if path in ("/identity/profiles", "/user-preferences"):
        return _resp({})
    return _resp([])


def test_pages_lists_and_walks_years(tmp_path: Path) -> None:
    env = {"SPARKYFITNESS_URL": "http://s/", "SPARKYFITNESS_API_KEY": "k" * 64}
    with patch("requests.Session.request", side_effect=_route) as req:
        result = SparkyFitnessWorker({"name": "sparky", "type": "sparkyfitness"}).run(
            _ctx(tmp_path, env)
        )
    files = _archive(result)
    assert [f["id"] for f in files["foods"]] == ["f1", "f2"]
    assert [s["id"] for s in files["exercise_sessions"]] == ["s1", "s2"]
    assert files["workout_presets"] == [{"id": "w1"}]
    assert files["food_entries"] == [{"id": "fe2024"}]
    assert files["custom_measurements"] == [{"id": "cm2025"}]
    # Goals are only asked for in years that had entries.
    assert set(files["goals_by_date"]) == {"2024-01-01", "2025-01-01"}

    calls = req.call_args_list
    assert "http://s/api/food-entries/range/2000-01-01/2000-12-31" in [c.args[1] for c in calls]
    assert {c.args[0] for c in calls} == {"GET"}
    assert all(c.kwargs["timeout"] for c in calls)
    assert result.output_files[0].name.startswith("sparky_")


def test_rejected_key_fails_with_hint(tmp_path: Path) -> None:
    resp = MagicMock()
    resp.raise_for_status.side_effect = requests.HTTPError("401 Client Error: Unauthorized")
    env = {"SPARKYFITNESS_URL": "http://s", "SPARKYFITNESS_API_KEY": "x"}
    with patch("requests.Session.request", return_value=resp):
        worker = SparkyFitnessWorker({"name": "sparky", "type": "sparkyfitness"})
        ctx = _ctx(tmp_path, env)
        with pytest.raises(BackupError, match="API key exists"):
            worker.run(ctx)


def test_missing_key_fails(tmp_path: Path) -> None:
    worker = SparkyFitnessWorker({"name": "sparky", "type": "sparkyfitness"})
    ctx = _ctx(tmp_path, {"SPARKYFITNESS_URL": "http://s"})
    with pytest.raises(BackupError, match="SPARKYFITNESS_API_KEY"):
        worker.run(ctx)


def test_scan_starts_at_the_account_creation_year():
    from datetime import date

    from app.workers.sparkyfitness import _FIRST_YEAR, _first_year

    assert _first_year({"created_at": "2024-03-01T10:00:00Z"}) == 2024
    assert _first_year({"createdAt": "2025-01-02"}) == 2025
    assert _first_year({}) == _FIRST_YEAR
    assert _first_year({"created_at": f"{date.today().year + 5}-01-01"}) == _FIRST_YEAR
