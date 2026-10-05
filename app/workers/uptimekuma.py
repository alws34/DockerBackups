"""Worker exporting Uptime Kuma's configuration over its Socket.IO API.

Uptime Kuma has no REST API for its configuration: the web UI logs in over
Socket.IO, after which the server pushes every list (monitors, notifications,
…) as events. This worker does the same, read-only, and never sends a write
event. Verified against the server source of 1.23.x and 2.x.
"""

from __future__ import annotations

import base64
import hmac
import re
import threading
import time
from datetime import datetime
from typing import ClassVar

import requests
import socketio

from app.core.context import BackupContext, BackupError, BackupResult
from app.workers.base import BackupWorker, EnvVarSpec, fetch_json

_TIMEOUT = 60

# Pushed by the server's afterLogin() to every client that logs in, in both 1.23 and 2.x.
_PUSHED = {
    "monitorList": "monitors",
    "notificationList": "notifications",
    "proxyList": "proxies",
    "dockerHostList": "docker_hosts",
    "apiKeyList": "api_keys",  # metadata only: the server never sends the key itself
    "maintenanceList": "maintenance",
    "statusPageList": "status_pages",
    "info": "info",
}
# Also pushed after login, but only by 2.x and later.
_OPTIONAL = {"remoteBrowserList": "remote_browsers"}

_SECRET_SETTING = re.compile(r"key|secret|password|token", re.IGNORECASE)


class UptimeKumaWorker(BackupWorker):
    """Export monitors, notifications, status pages, maintenance and settings."""

    worker_type: ClassVar[str] = "uptimekuma"
    display_name: ClassVar[str] = "Uptime Kuma"
    description: ClassVar[str] = (
        "Exports monitors, notifications, proxies, status pages with their monitor groups, "
        "maintenance windows, tags, docker hosts, API key names and settings; heartbeat and "
        "uptime history are not included."
    )
    env_var_specs: ClassVar[list[EnvVarSpec]] = [
        EnvVarSpec(
            key="UPTIMEKUMA_URL",
            label="Uptime Kuma URL",
            description="Base URL of your Uptime Kuma instance (e.g. https://status.example.com).",
            secret=False,
            required=True,
        ),
        EnvVarSpec(
            key="UPTIMEKUMA_USERNAME",
            label="Username",
            description="Your Uptime Kuma login (Uptime Kuma has a single user).",
            secret=False,
            required=True,
        ),
        EnvVarSpec(
            key="UPTIMEKUMA_PASSWORD",
            label="Password",
            description="Password of that Uptime Kuma account.",
            secret=True,
            required=True,
        ),
        EnvVarSpec(
            key="UPTIMEKUMA_TOTP_SECRET",
            label="2FA secret",
            description=(
                "Only if 2FA is on: the secret= value of the otpauth:// URI shown by "
                "'Show URI' when enabling 2FA. Leave empty otherwise."
            ),
            secret=True,
            required=False,
        ),
    ]

    def run(self, context: BackupContext) -> BackupResult:
        started_at = datetime.now()
        url = self.require_env(context, "UPTIMEKUMA_URL").rstrip("/")
        username = self.require_env(context, "UPTIMEKUMA_USERNAME")
        password = self.require_env(context, "UPTIMEKUMA_PASSWORD")
        totp_secret = context.env.get("UPTIMEKUMA_TOTP_SECRET", "").strip()

        got: dict[str, object] = {}
        arrived = threading.Condition()
        sio = socketio.Client(reconnection=False, request_timeout=_TIMEOUT)

        # Handlers run on their own threads, so arrival order is not guaranteed.
        @sio.on("*")
        def _collect(event: str, *args: object) -> None:
            if event in _PUSHED or event in _OPTIONAL:
                with arrived:
                    got[event] = args[0] if args else None
                    arrived.notify_all()

        def call(event: str, data: object = None) -> dict:
            try:
                res = sio.call(event, data, timeout=_TIMEOUT)
            except socketio.exceptions.SocketIOError as e:
                raise BackupError(f"Uptime Kuma did not answer '{event}' in time: {e}") from e
            if not isinstance(res, dict):
                raise BackupError(f"Uptime Kuma sent an unexpected reply to '{event}': {res!r}")
            return res

        def need(event: str, data: object = None) -> dict:
            res = call(event, data)
            if not res.get("ok"):
                raise BackupError(f"Uptime Kuma refused '{event}': {res.get('msg')}")
            return res

        try:
            sio.connect(url, wait_timeout=_TIMEOUT)
        except socketio.exceptions.ConnectionError as e:
            raise BackupError(f"Could not connect to Uptime Kuma at {url}: {e}") from e

        try:
            # Like the web UI: send the 2FA code only when the server asks for it.
            creds = {"username": username, "password": password}
            res = call("login", creds)
            if res.get("tokenRequired"):
                if not totp_secret:
                    raise BackupError(
                        "This Uptime Kuma account has 2FA enabled. Set UPTIMEKUMA_TOTP_SECRET "
                        "or turn 2FA off (Settings → Security)."
                    )
                res = call("login", {**creds, "token": totp(totp_secret)})
            if not res.get("ok"):
                raise BackupError(
                    f"Uptime Kuma login failed ({res.get('msg')}). Check UPTIMEKUMA_USERNAME, "
                    "UPTIMEKUMA_PASSWORD and, with 2FA, UPTIMEKUMA_TOTP_SECRET (each 2FA code "
                    "is accepted once, so two runs within 30 seconds fail)."
                )

            def missing() -> list[str]:
                # An "info" without version is the one sent to every client before login.
                version = str((got.get("info") or {}).get("version", ""))
                if not version:
                    return ["info", *(k for k in _PUSHED if k not in got)]
                major = version.split(".")[0]
                wanted = [*_PUSHED, *(_OPTIONAL if major.isdigit() and int(major) >= 2 else ())]
                return [k for k in wanted if k not in got]

            with arrived:
                if not arrived.wait_for(lambda: not missing(), timeout=_TIMEOUT):
                    raise BackupError(f"Uptime Kuma logged in but never sent: {missing()}")
                pushed = dict(got)

            maintenance = []
            for item in _as_list(pushed["maintenanceList"]):
                mid = item["id"]
                maintenance.append(
                    {
                        **item,
                        "monitors": need("getMonitorMaintenance", mid)["monitors"],
                        "status_pages": need("getMaintenanceStatusPage", mid)["statusPages"],
                    }
                )
            tags = need("getTags")["tags"]
            settings = need("getSettings")["data"]
        finally:
            sio.disconnect()

        # The monitor groups shown on a status page are only served by its public JSON endpoint
        # (the same one the status page itself loads); read-only and unauthenticated.
        status_pages = []
        with requests.Session() as s:
            for page in _as_list(pushed["statusPageList"]):
                public = fetch_json(s, "GET", f"{url}/api/status-page/{page['slug']}")
                status_pages.append(
                    {"config": page, **{k: v for k, v in public.items() if k != "config"}}
                )

        # Monitors arrive keyed by id, the other lists as arrays.
        files: dict[str, object] = {
            name: _as_list(pushed[event])
            for event, name in (_PUSHED | _OPTIONAL).items()
            if event in pushed
        }
        files |= {
            "status_pages": status_pages,
            "maintenance": maintenance,
            "tags": tags,
            "settings": {k: v for k, v in settings.items() if not _SECRET_SETTING.search(k)},
            "info": pushed["info"],
        }
        return self.archive_json(context, started_at, files)


def _as_list(data: object) -> list:
    """Return a pushed list's items, whether the server sent an array or an id-keyed object."""
    return list(data.values()) if isinstance(data, dict) else list(data or [])


def totp(secret_b32: str, at: float | None = None) -> str:
    """RFC 6238 code (SHA-1, 30 s, 6 digits), the scheme Uptime Kuma's 2FA uses."""
    secret = secret_b32.replace(" ", "").upper()
    try:
        key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    except ValueError as e:
        raise BackupError("UPTIMEKUMA_TOTP_SECRET is not a valid base32 secret") from e
    counter = int((time.time() if at is None else at) // 30).to_bytes(8, "big")
    digest = hmac.new(key, counter, "sha1").digest()
    offset = digest[-1] & 0x0F
    code = int.from_bytes(digest[offset : offset + 4]) & 0x7FFFFFFF
    return f"{code % 1_000_000:06d}"
