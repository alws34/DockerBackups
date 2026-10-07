"""Worker creating and downloading a native Home Assistant backup via its websocket API."""

from __future__ import annotations

import itertools
import json
import logging
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import ClassVar

import requests
from websockets.exceptions import WebSocketException
from websockets.sync.client import ClientConnection, connect

from app.core.context import BackupContext, BackupError, BackupResult
from app.workers.base import BackupWorker, EnvVarSpec

logger = logging.getLogger(__name__)

# HA's local backup agent: "hassio.local" on HA OS / Supervised, "backup.local" on Core/Container.
_LOCAL_AGENTS = ("hassio.local", "backup.local")
# Folders Supervisor installs can add; "media" is left out because it can be huge.
_SUPERVISOR_FOLDERS = ["share", "ssl", "addons/local"]
_POLL_SECONDS = 5
_CREATE_TIMEOUT_SECONDS = 2 * 3600
_REPLY_TIMEOUT_SECONDS = 120

Call = Callable[..., dict]


class HomeAssistantWorker(BackupWorker):
    """Trigger a Home Assistant backup, wait for it, download the .tar, then delete it in HA.

    Backups made with ``backup/generate`` are "manual" backups, which HA's own
    retention never prunes, so the HA-side copy is removed after the download
    to stop them piling up on HA's disk.
    """

    worker_type: ClassVar[str] = "homeassistant"
    display_name: ClassVar[str] = "Home Assistant"
    description: ClassVar[str] = (
        "Creates a native Home Assistant backup (configuration, database and, on HA OS, "
        "add-ons plus the share/ssl folders) and downloads the restorable .tar, encrypted "
        "with your HA backup encryption key; the media folder is not included."
    )
    env_var_specs: ClassVar[list[EnvVarSpec]] = [
        EnvVarSpec(
            key="HOMEASSISTANT_URL",
            label="Home Assistant URL",
            description="Base URL of your Home Assistant (e.g. http://homeassistant.local:8123).",
            secret=False,
            required=True,
        ),
        EnvVarSpec(
            key="HOMEASSISTANT_TOKEN",
            label="Long-lived access token",
            description=(
                "Log in as an administrator → click your user (Profile) → Security tab → "
                "Long-lived access tokens → Create token."
            ),
            secret=True,
            required=True,
        ),
    ]

    def run(self, context: BackupContext) -> BackupResult:
        started_at = datetime.now()
        base = self.require_env(context, "HOMEASSISTANT_URL").rstrip("/")
        token = self.require_env(context, "HOMEASSISTANT_TOKEN")
        stamp = started_at.strftime("%Y%m%d_%H%M%S")
        ws_url = "ws" + base.removeprefix("http") + "/api/websocket"

        try:
            with connect(ws_url, open_timeout=30, max_size=None) as ws:
                call = _login(ws, token)
                backup_id, agent_id, protected = self._create_backup(
                    call, f"Homelab Takeout {self.service_name} {stamp}"
                )
                try:
                    archive = self._download(context, base, token, backup_id, agent_id, stamp)
                finally:
                    # Best effort: a failed delete must not hide the real result.
                    try:
                        call("backup/delete", backup_id=backup_id)
                    except (BackupError, OSError, WebSocketException) as e:
                        logger.warning(
                            f"Could not delete backup {backup_id} in Home Assistant: {e}"
                        )
        except (OSError, WebSocketException) as e:
            raise BackupError(f"Home Assistant websocket {ws_url} failed: {e}") from e

        self.cleanup_old_files(archive.parent, f"{self.service_name}_*.tar", context.retention_days)
        state = "encrypted with your HA backup key" if protected else "NOT encrypted"
        return BackupResult(
            service_name=self.service_name,
            worker_type=self.worker_type,
            success=True,
            message=f"{archive.name} ({archive.stat().st_size} bytes, {state})",
            output_files=[archive],
            started_at=started_at,
            finished_at=datetime.now(),
        )

    def _create_backup(self, call: Call, name: str) -> tuple[str, str, bool]:
        """Start a backup on HA's local agent and wait until HA has finished writing it."""
        agents = {a["agent_id"] for a in call("backup/agents/info")["agents"]}
        agent_id = next((a for a in _LOCAL_AGENTS if a in agents), None)
        if not agent_id:
            raise BackupError(f"Home Assistant has no local backup agent (found: {sorted(agents)})")
        # The same key HA uses for its automatic backups (null if never set up).
        password = call("backup/config/info")["config"]["create_backup"]["password"]
        extra = {}
        if agent_id == "hassio.local":
            extra = {"include_all_addons": True, "include_folders": _SUPERVISOR_FOLDERS}
        call(
            "backup/generate",
            agent_ids=[agent_id],
            include_database=True,
            include_homeassistant=True,
            name=name,
            password=password,
            **extra,
        )

        deadline = time.monotonic() + _CREATE_TIMEOUT_SECONDS
        while (info := call("backup/info"))["state"] != "idle":
            if time.monotonic() > deadline:
                raise BackupError(
                    f"Home Assistant backup still running after {_CREATE_TIMEOUT_SECONDS}s"
                )
            time.sleep(_POLL_SECONDS)

        event = info.get("last_action_event") or {}
        if event.get("state") != "completed":
            raise BackupError(
                f"Home Assistant backup failed (reason: {event.get('reason')}); "
                "check Settings → System → Logs in Home Assistant"
            )
        backup = next((b for b in info["backups"] if b["name"] == name), None)
        if not backup or agent_id not in backup["agents"]:
            raise BackupError(
                f"Home Assistant finished but backup '{name}' is not on {agent_id} "
                f"(agent errors: {info.get('agent_errors')})"
            )
        return backup["backup_id"], agent_id, backup["agents"][agent_id]["protected"]

    def _download(
        self,
        context: BackupContext,
        base: str,
        token: str,
        backup_id: str,
        agent_id: str,
        stamp: str,
    ) -> Path:
        backup_dir = self.service_backup_dir(context)
        backup_dir.mkdir(parents=True, exist_ok=True)
        archive = backup_dir / f"{self.service_name}_{stamp}.tar"
        url = f"{base}/api/backup/download/{backup_id}"
        try:
            with requests.get(
                url,
                params={"agent_id": agent_id},
                headers={"Authorization": f"Bearer {token}"},
                stream=True,
                timeout=(30, 600),
            ) as resp:
                resp.raise_for_status()
                with archive.open("wb") as f:
                    for chunk in resp.iter_content(chunk_size=1 << 20):
                        f.write(chunk)
        except OSError as e:  # includes requests.RequestException
            archive.unlink(missing_ok=True)
            raise BackupError(f"Downloading backup from {url} failed: {e}") from e
        archive.chmod(0o600)
        return archive


def _login(ws: ClientConnection, token: str) -> Call:
    """Authenticate the websocket and return a ``call(type, **params)`` helper."""
    ws.recv(timeout=_REPLY_TIMEOUT_SECONDS)  # auth_required
    ws.send(json.dumps({"type": "auth", "access_token": token}))
    if json.loads(ws.recv(timeout=_REPLY_TIMEOUT_SECONDS)).get("type") != "auth_ok":
        raise BackupError(
            "Home Assistant rejected HOMEASSISTANT_TOKEN; create a new long-lived access "
            "token under Profile → Security"
        )
    ids = itertools.count(1)

    def call(msg_type: str, **params: object) -> dict:
        msg_id = next(ids)
        ws.send(json.dumps({"id": msg_id, "type": msg_type, **params}))
        msg: dict = {}
        while msg.get("id") != msg_id:  # skip events and replies to other calls
            msg = json.loads(ws.recv(timeout=_REPLY_TIMEOUT_SECONDS))
        if not msg.get("success"):
            err = msg.get("error") or {}
            hint = (
                " (the token's user must be an administrator)"
                if err.get("code") == "unauthorized"
                else ""
            )
            raise BackupError(f"Home Assistant {msg_type} failed: {err.get('message', err)}{hint}")
        return msg["result"]

    return call
