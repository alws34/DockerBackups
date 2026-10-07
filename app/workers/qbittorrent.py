"""Worker exporting qBittorrent's torrents, .torrent files and settings via the WebUI API v2."""

from __future__ import annotations

import json
import re
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path
from typing import ClassVar
from urllib.parse import urlsplit

import requests

from app.core.context import BackupContext, BackupError, BackupResult
from app.workers.base import BackupWorker, EnvVarSpec, fetch_json

# Preference keys that hold credentials. qBittorrent never returns the WebUI
# password itself, but strip anything that looks like a secret in case a
# version does (proxy/email/DynDNS passwords, API keys, tokens, hashes).
_SECRET_KEY = re.compile(r"password|passwd|secret|token|api_?key|_hash$|salt", re.IGNORECASE)


def strip_secrets(prefs: dict) -> tuple[dict, list[str]]:
    """Return ``prefs`` without secret-looking keys, plus the sorted list of removed keys."""
    removed = sorted(k for k in prefs if _SECRET_KEY.search(k))
    return {k: v for k, v in prefs.items() if k not in removed}, removed


class QBittorrentWorker(BackupWorker):
    """Export torrents (with their .torrent files), categories, tags, RSS and settings."""

    worker_type: ClassVar[str] = "qbittorrent"
    display_name: ClassVar[str] = "qBittorrent"
    description: ClassVar[str] = (
        "Exports the torrent list with each torrent's properties, trackers, files and "
        ".torrent file, plus categories, tags, RSS feeds and rules and the app settings "
        "(passwords removed); downloaded content is not included."
    )
    env_var_specs: ClassVar[list[EnvVarSpec]] = [
        EnvVarSpec(
            key="QBITTORRENT_URL",
            label="qBittorrent WebUI URL",
            description=(
                "The address you open the WebUI at (e.g. http://192.168.0.2:8080). "
                "Enabled in Tools → Options → Web UI."
            ),
            secret=False,
            required=True,
        ),
        EnvVarSpec(
            key="QBITTORRENT_USERNAME",
            label="Username",
            description="WebUI username (Tools → Options → Web UI → Authentication).",
            secret=False,
            required=True,
        ),
        EnvVarSpec(
            key="QBITTORRENT_PASSWORD",
            label="Password",
            description="WebUI password (Tools → Options → Web UI → Authentication).",
            secret=True,
            required=True,
        ),
    ]

    def run(self, context: BackupContext) -> BackupResult:
        started_at = datetime.now()
        base = self.require_env(context, "QBITTORRENT_URL").rstrip("/")
        username = self.require_env(context, "QBITTORRENT_USERNAME")
        password = self.require_env(context, "QBITTORRENT_PASSWORD")
        api = f"{base}/api/v2"
        origin = "{0.scheme}://{0.netloc}".format(urlsplit(base))

        with requests.Session() as s:
            # qBittorrent's CSRF check rejects requests whose Referer/Origin
            # don't match the Host it was reached at.
            s.headers.update({"Referer": f"{base}/", "Origin": origin})
            self._login(s, api, username, password)
            try:
                files, torrent_files, notes = self._collect(s, api)
            finally:
                try:
                    s.request("POST", f"{api}/auth/logout", timeout=30)
                except requests.RequestException:
                    pass  # session expires on its own; a failed logout must not fail the backup

        return self._write_archive(context, started_at, files, torrent_files, notes)

    def _login(self, s: requests.Session, api: str, username: str, password: str) -> None:
        try:
            resp = s.request(
                "POST",
                f"{api}/auth/login",
                data={"username": username, "password": password},
                timeout=30,
            )
        except requests.RequestException as e:
            raise BackupError(f"Can't reach qBittorrent at {api}: {e}") from e
        if resp.status_code == 403:
            raise BackupError(
                "Login blocked (HTTP 403): qBittorrent has banned this IP after too many "
                "failed logins. Wait for the ban to expire (Options → Web UI → ban duration) "
                "or restart qBittorrent, then check the username/password."
            )
        if resp.status_code == 401 or resp.text.strip() == "Fails.":
            raise BackupError(
                "Login refused: check the username/password, or the WebUI's IP ban "
                "after failed logins."
            )
        if resp.status_code != 200:
            raise BackupError(
                f"Login failed (HTTP {resp.status_code}): check QBITTORRENT_URL points at "
                "the WebUI and that its host-header/CSRF protection allows this address."
            )

    def _text(self, s: requests.Session, url: str) -> str:
        try:
            resp = s.request("GET", url, timeout=120)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise BackupError(f"GET {url} failed: {e}") from e
        return resp.text.strip()

    def _collect(self, s: requests.Session, api: str) -> tuple[dict, dict[str, bytes], dict]:
        def get(path: str, **params: str) -> dict | list:
            return fetch_json(s, "GET", f"{api}{path}", params=params)

        torrents = get("/torrents/info")
        details: dict[str, dict] = {}
        torrent_files: dict[str, bytes] = {}
        not_exported: dict[str, str] = {}
        for t in torrents:
            h = t["hash"]
            details[h] = {
                "properties": get("/torrents/properties", hash=h),
                "trackers": get("/torrents/trackers", hash=h),
                "files": get("/torrents/files", hash=h),
            }
            # /torrents/export exists since qBittorrent 4.5 (404 before that, or
            # if the torrent vanished mid-run); 409 = magnet without metadata yet.
            try:
                resp = s.request("GET", f"{api}/torrents/export", params={"hash": h}, timeout=120)
            except requests.RequestException as e:
                raise BackupError(f"GET {api}/torrents/export failed: {e}") from e
            if resp.status_code in (404, 409):
                not_exported[h] = f"HTTP {resp.status_code}"
                continue
            try:
                resp.raise_for_status()
            except requests.RequestException as e:
                raise BackupError(f"GET {api}/torrents/export failed: {e}") from e
            torrent_files[h] = resp.content

        prefs, redacted = strip_secrets(get("/app/preferences"))
        files = {
            "version": {
                "app": self._text(s, f"{api}/app/version"),
                "webapi": self._text(s, f"{api}/app/webapiVersion"),
                "build": get("/app/buildInfo"),
            },
            "torrents": torrents,
            "torrent_details": details,
            "categories": get("/torrents/categories"),
            "tags": get("/torrents/tags"),
            "rss_items": get("/rss/items", withData="false"),
            "rss_rules": get("/rss/rules"),
            "preferences": prefs,
        }
        notes = {
            "preferences_removed": redacted,
            "torrents_not_exported": not_exported,
            "not_included": "Downloaded content (the files on disk) is not exported.",
        }
        if torrents and len(not_exported) == len(torrents):
            notes["torrent_export"] = (
                "No .torrent files could be exported; /torrents/export needs qBittorrent 4.5+."
            )
        return files, torrent_files, notes

    def _write_archive(
        self,
        context: BackupContext,
        started_at: datetime,
        files: dict,
        torrent_files: dict[str, bytes],
        notes: dict,
    ) -> BackupResult:
        backup_dir = self.service_backup_dir(context)
        backup_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{self.service_name}_{started_at.strftime('%Y%m%d_%H%M%S')}"
        archive = backup_dir / f"{stem}.tar.gz"

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            for name, data in {**files, "notes": notes}.items():
                (work / f"{name}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False))
            (work / "torrents").mkdir()
            for h, content in torrent_files.items():
                (work / "torrents" / f"{h}.torrent").write_bytes(content)
            with tarfile.open(archive, "w:gz") as tar:
                tar.add(work, arcname=stem)

        archive.chmod(0o600)
        self.cleanup_old_files(backup_dir, f"{self.service_name}_*.tar.gz", context.retention_days)

        skipped = len(notes["torrents_not_exported"])
        return BackupResult(
            service_name=self.service_name,
            worker_type=self.worker_type,
            success=True,
            message=(
                f"{len(files['torrents'])} torrents, {len(torrent_files)} .torrent files"
                f"{f' ({skipped} skipped)' if skipped else ''}, "
                f"{len(files['categories'])} categories, {len(files['tags'])} tags: "
                f"{archive.name} ({archive.stat().st_size} bytes)"
            ),
            output_files=[archive],
            started_at=started_at,
            finished_at=datetime.now(),
        )
