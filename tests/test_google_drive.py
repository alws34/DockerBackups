"""Google Drive uploads, listing and retention against an in-memory Drive (no network)."""

from __future__ import annotations  # FakeDrive.list shadows the builtin in annotations

import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError

from app.core.context import BackupError
from app.destinations import google_drive
from app.destinations.google_drive import GoogleDriveDestination

FOLDER = "application/vnd.google-apps.folder"


class _Request:
    def __init__(self, result):
        self.result = result

    def execute(self):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeDrive:
    """Just enough of the Drive v3 ``files()`` API for the destination."""

    page_size = 2  # small, so listings span several pages

    def __init__(self):
        self.items: dict[str, dict] = {}  # id -> {name, parents, mimeType}
        self.uploads: list[tuple[str, str]] = []  # (name, local path)
        self.deleted: list[str] = []

    def files(self):
        return self

    def add(self, name: str, parent: str, mime: str = "") -> str:
        file_id = f"id{len(self.items) + len(self.deleted)}"
        self.items[file_id] = {"name": name, "parents": [parent], "mimeType": mime}
        return file_id

    def list(self, q, fields, pageSize=None, pageToken=None):
        folder = re.fullmatch(r"name='(.*)' and '(.*)' in parents and mimeType='(.*)' .*", q)
        if folder:
            name, parent = folder.group(1).replace("\\'", "'"), folder.group(2)
            hits = [
                {"id": i}
                for i, f in self.items.items()
                if f["name"] == name and parent in f["parents"] and f["mimeType"] == FOLDER
            ]
            return _Request({"files": hits})
        parent = re.fullmatch(r"'(.*)' in parents and trashed=false", q).group(1)
        children = [
            {"id": i, "name": f["name"]} for i, f in self.items.items() if parent in f["parents"]
        ]
        start = int(pageToken or 0)
        page = {"files": children[start : start + self.page_size]}
        if start + self.page_size < len(children):
            page["nextPageToken"] = str(start + self.page_size)
        return _Request(page)

    def create(self, body, fields, media_body=None):
        file_id = self.add(body["name"], body["parents"][0], body.get("mimeType", ""))
        if media_body is not None:
            self.uploads.append((body["name"], media_body._filename))
        return _Request({"id": file_id})

    def delete(self, fileId):
        self.deleted.append(self.items.pop(fileId)["name"])
        return _Request({})

    def names_in(self, parent: str) -> list[str]:
        return sorted(f["name"] for f in self.items.values() if parent in f["parents"])

    def folder_id(self, name: str) -> str:
        return next(i for i, f in self.items.items() if f["name"] == name)


def _dest(drive: FakeDrive, folder_id: str = "") -> GoogleDriveDestination:
    dest = GoogleDriveDestination({"refresh_token": "rt", "client_id": "cid"}, folder_id)
    dest._service = drive
    return dest


def test_ship_uploads_into_service_folder_and_prunes_only_own_files(tmp_path: Path):
    drive = FakeDrive()
    dest = _dest(drive)
    for day in (1, 2, 3, 4):
        src = tmp_path / f"wikijs_2026100{day}_033000.tar.gz"
        src.write_text(str(day))
        pruned = dest.ship(src, "wikijs", keep_count=2)
        if day == 1:
            # The folder is created on first use, then reused; files the user put
            # there (even ones starting "wikijs") are never pruned.
            folder = drive.folder_id("wikijs")
            drive.add("my-notes.txt", folder)
            drive.add("wikijs-old-export.zip", folder)
    assert pruned == ["wikijs_20261002_033000.tar.gz"]
    root = drive.folder_id("Homelab Takeout")
    assert drive.items[root]["parents"] == ["root"]
    assert drive.names_in(root) == ["wikijs"]
    assert drive.names_in(drive.folder_id("wikijs")) == [
        "my-notes.txt",
        "wikijs-old-export.zip",
        "wikijs_20261003_033000.tar.gz",
        "wikijs_20261004_033000.tar.gz",
    ]
    assert drive.uploads[-1] == ("wikijs_20261004_033000.tar.gz", str(src))


def test_configured_folder_id_is_used_as_root(tmp_path: Path):
    drive = FakeDrive()
    src = tmp_path / "n8n_20261001_000000.json"
    src.write_text("{}")
    _dest(drive, folder_id="myFolder").put(src, "n8n")
    assert drive.items[drive.folder_id("n8n")]["parents"] == ["myFolder"]
    assert "Homelab Takeout" not in [f["name"] for f in drive.items.values()]


def test_service_name_is_escaped_in_drive_queries():
    drive = FakeDrive()
    dest = _dest(drive, folder_id="root")
    first = dest._find_or_create_folder("it's", "root")
    assert dest._find_or_create_folder("it's", "root") == first  # found, not duplicated
    assert google_drive._quote("a\\'b") == "a\\\\\\'b"


def test_remove_looks_up_ids_when_not_listed_yet():
    drive = FakeDrive()
    dest = _dest(drive, folder_id="root")
    folder = dest._service_folder("n8n")
    drive.add("n8n_20261001_000000.json", folder)
    fresh = _dest(drive, folder_id="root")
    fresh.remove("n8n", "n8n_20261001_000000.json")
    fresh.remove("n8n", "n8n_gone.json")  # already gone: nothing to do
    assert drive.names_in(folder) == []


def test_expired_login_and_api_errors_become_backup_errors():
    dest = _dest(FakeDrive())
    with pytest.raises(BackupError, match="expired or was revoked"):
        dest._call(_Request(RefreshError("invalid_grant")))
    resp = MagicMock(status=403, reason="Forbidden")
    with pytest.raises(BackupError, match="Google Drive error: Forbidden"):
        dest._call(_Request(HttpError(resp, b"{}")))


def test_check_reports_account_and_prepares_root():
    drive = FakeDrive()
    drive.about = lambda: MagicMock(
        get=lambda fields: _Request({"user": {"emailAddress": "me@x.com"}})
    )
    dest = _dest(drive)
    assert dest.check() == "Connected as me@x.com."
    assert dest.folder_id == drive.folder_id("Homelab Takeout")


def test_drive_client_built_from_stored_refresh_token():
    dest = GoogleDriveDestination(
        {"refresh_token": "rt", "client_id": "cid", "client_secret": "cs"}, ""
    )
    with patch("googleapiclient.discovery.build", return_value="svc") as build:
        assert dest._drive() == "svc"
        assert dest._drive() == "svc"
    build.assert_called_once()
    creds = build.call_args.kwargs["credentials"]
    assert creds.refresh_token == "rt" and creds.client_id == "cid"
    assert creds.scopes == ["https://www.googleapis.com/auth/drive.file"]


def test_from_env_requires_a_readable_login(tmp_path: Path, monkeypatch):
    tokens = tmp_path / "google-tokens.json"
    monkeypatch.setenv("GOOGLE_TOKENS_FILE", str(tokens))
    with pytest.raises(BackupError, match="not connected"):
        GoogleDriveDestination.from_env({})
    tokens.write_text("{broken")
    with pytest.raises(BackupError, match="unreadable"):
        GoogleDriveDestination.from_env({})
    assert GoogleDriveDestination.login_status({}) == {"connected": True, "account": ""}
    tokens.write_text(json.dumps({"refresh_token": "rt", "client_id": "cid"}))
    dest = GoogleDriveDestination.from_env({"GOOGLE_DRIVE_FOLDER_ID": " abc "})
    assert dest.folder_id == "abc" and dest.token_data["refresh_token"] == "rt"


def test_disconnect_deletes_tokens_even_if_revoke_fails(tmp_path: Path, monkeypatch):
    tokens = tmp_path / "google-tokens.json"
    tokens.write_text(json.dumps({"refresh_token": "rt"}))
    monkeypatch.setenv("GOOGLE_TOKENS_FILE", str(tokens))
    with patch.object(
        google_drive.requests, "post", side_effect=google_drive.requests.ConnectionError()
    ):
        GoogleDriveDestination.disconnect({})
    assert not tokens.exists()
    GoogleDriveDestination.disconnect({})  # nothing stored: no error


def test_device_login_start_errors(monkeypatch):
    env = {"GOOGLE_DEVICE_CLIENT_ID": "cid"}
    bad = MagicMock(ok=False)
    bad.json.return_value = {"error": "invalid_client"}
    with patch.object(google_drive.requests, "post", return_value=bad):
        with pytest.raises(BackupError, match="not valid for device login"):
            GoogleDriveDestination.start_login(env)
    with patch.object(
        google_drive.requests, "post", side_effect=google_drive.requests.ConnectionError("down")
    ):
        with pytest.raises(BackupError, match="Could not reach Google"):
            GoogleDriveDestination.start_login(env)


def test_device_login_expires(monkeypatch):
    monkeypatch.setattr(google_drive.time, "sleep", lambda s: None)
    with pytest.raises(BackupError, match="expired"):
        google_drive._poll_for_tokens("cid", "s", "dc", 1, 0)
