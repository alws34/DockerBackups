"""Tests for the Home Assistant worker with a fake websocket and mocked download."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.core.context import BackupContext, BackupError
from app.workers import homeassistant
from app.workers.homeassistant import HomeAssistantWorker

ENV = {"HOMEASSISTANT_URL": "https://ha.example", "HOMEASSISTANT_TOKEN": "tok"}


class FakeWS:
    """Answers HA websocket commands; ``backup/info`` reports busy ``busy_polls`` times."""

    def __init__(self, agents=("backup.local",), outcome="completed", busy_polls=2) -> None:
        self.agents = agents
        self.outcome = outcome
        self.busy_polls = busy_polls
        self.sent: list[dict] = []
        self.inbox = [{"type": "auth_required"}]
        self.name = ""

    def __enter__(self) -> FakeWS:
        return self

    def __exit__(self, *exc: object) -> None:
        pass

    def recv(self, timeout: float | None = None) -> str:
        return json.dumps(self.inbox.pop(0))

    def send(self, raw: str) -> None:
        msg = json.loads(raw)
        self.sent.append(msg)
        if msg["type"] == "auth":
            ok = msg["access_token"] == "tok"
            self.inbox.append({"type": "auth_ok" if ok else "auth_invalid"})
            return
        result = self.answer(msg)
        # An unrelated event first, to check replies are matched by id.
        self.inbox += [
            {"id": 999, "type": "event"},
            {"id": msg["id"], "type": "result", "success": True, "result": result},
        ]

    def answer(self, msg: dict) -> object:
        t = msg["type"]
        if t == "backup/agents/info":
            return {"agents": [{"agent_id": a} for a in self.agents]}
        if t == "backup/config/info":
            return {"config": {"create_backup": {"password": "ha-key"}}}
        if t == "backup/generate":
            self.name = msg["name"]
            return {"backup_job_id": "job1"}
        if t == "backup/info":
            if self.busy_polls:
                self.busy_polls -= 1
                return {"state": "create_backup", "backups": []}
            agent = self.agents[0]
            return {
                "state": "idle",
                "last_action_event": {"state": self.outcome, "reason": "upload_failed"},
                "backups": [
                    {"backup_id": "old", "name": "Automatic backup", "agents": {}},
                    {
                        "backup_id": "abc123",
                        "name": self.name,
                        "agents": {agent: {"protected": True}},
                    },
                ],
                "agent_errors": {},
            }
        if t == "backup/delete":
            return {"agent_errors": {}}
        raise AssertionError(t)


def _ctx(tmp_path: Path) -> BackupContext:
    return BackupContext(
        backup_root=tmp_path / "backups",
        log_root=tmp_path / "logs",
        state_root=tmp_path / "state",
        retention_days=30,
        env=ENV,
    )


def _download() -> MagicMock:
    resp = MagicMock()
    resp.__enter__.return_value = resp
    resp.iter_content.return_value = [b"tar-", b"bytes"]
    return resp


def _run(tmp_path: Path, ws: FakeWS) -> tuple[object, MagicMock]:
    worker = HomeAssistantWorker({"name": "homeassistant", "type": "homeassistant"})
    with (
        patch.object(homeassistant, "connect", return_value=ws) as connect,
        patch.object(homeassistant, "_POLL_SECONDS", 0),
        patch("requests.get", return_value=_download()) as get,
    ):
        result = worker.run(_ctx(tmp_path))
    assert connect.call_args.args[0] == "wss://ha.example/api/websocket"
    return result, get


def test_creates_waits_downloads_and_deletes(tmp_path: Path) -> None:
    ws = FakeWS()
    result, get = _run(tmp_path, ws)

    archive = result.output_files[0]
    assert archive.name.startswith("homeassistant_") and archive.suffix == ".tar"
    assert archive.read_bytes() == b"tar-bytes"
    assert "encrypted" in result.message and "NOT" not in result.message

    types = [m["type"] for m in ws.sent]
    assert types.count("backup/info") == 3  # polled until idle
    gen = next(m for m in ws.sent if m["type"] == "backup/generate")
    assert gen["agent_ids"] == ["backup.local"] and gen["password"] == "ha-key"
    assert "include_all_addons" not in gen  # Core/Container rejects add-ons
    assert get.call_args.args[0] == "https://ha.example/api/backup/download/abc123"
    assert get.call_args.kwargs["params"] == {"agent_id": "backup.local"}
    assert ws.sent[-1] == {"id": ws.sent[-1]["id"], "type": "backup/delete", "backup_id": "abc123"}


def test_supervisor_includes_addons(tmp_path: Path) -> None:
    ws = FakeWS(agents=("hassio.local", "cloud.cloud"), busy_polls=0)
    _run(tmp_path, ws)
    gen = next(m for m in ws.sent if m["type"] == "backup/generate")
    assert gen["agent_ids"] == ["hassio.local"]
    assert gen["include_all_addons"] is True and "media" not in gen["include_folders"]


def test_failed_backup_raises(tmp_path: Path) -> None:
    with pytest.raises(BackupError, match="backup failed"):
        _run(tmp_path, FakeWS(outcome="failed"))


def test_bad_token_raises(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    ctx.env = {**ENV, "HOMEASSISTANT_TOKEN": "wrong"}
    worker = HomeAssistantWorker({"name": "homeassistant", "type": "homeassistant"})
    with patch.object(homeassistant, "connect", return_value=FakeWS()):
        with pytest.raises(BackupError, match="rejected"):
            worker.run(ctx)
