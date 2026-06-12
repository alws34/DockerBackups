"""Google Drive backup destination using OAuth2 user credentials."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, ClassVar

from app.core.context import BackupError, BackupResult
from app.destinations.base import BackupDestination
from app.workers.base import EnvVarSpec

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/drive.file"]
_CREDS_PATH = Path("/config/google-credentials.json")
_TOKENS_PATH = Path("/config/google-tokens.json")

# Env var names that control the Google Drive destination.
ENABLED_ENV = "GOOGLE_DRIVE_ENABLED"
FOLDER_ID_ENV = "GOOGLE_DRIVE_FOLDER_ID"


class GoogleDriveDestination(BackupDestination):
    """Upload backup files to a Google Drive folder via OAuth2 credentials."""

    destination_type: ClassVar[str] = "google_drive"
    display_name: ClassVar[str] = "Google Drive"
    description: ClassVar[str] = (
        "Upload backup files to a Google Drive folder via OAuth2. "
        "Upload your client_secret.json then click Authorize in the UI."
    )
    env_var_specs: ClassVar[list[EnvVarSpec]] = [
        EnvVarSpec(
            key=ENABLED_ENV,
            label="Enable Google Drive",
            description=(
                "Set to 'true' to upload backups to Google Drive "
                "after each successful run."
            ),
            secret=False,
            required=False,
        ),
        EnvVarSpec(
            key=FOLDER_ID_ENV,
            label="Drive Folder ID",
            description="Folder ID from the Drive URL (share it with your Google account).",
            secret=False,
            required=True,
        ),
    ]

    def __init__(self, credentials_file: Path, tokens_file: Path, folder_id: str) -> None:
        self.credentials_file = credentials_file
        self.tokens_file = tokens_file
        self.folder_id = folder_id
        self._service: Any = None

    def _get_service(self) -> Any:
        if self._service is not None:
            return self._service

        if not self.credentials_file.exists():
            raise BackupError(
                "Google client credentials not found. "
                "Upload client_secret.json in the Destinations panel."
            )
        if not self.tokens_file.exists():
            raise BackupError(
                "Google Drive not authorized. "
                "Click 'Authorize Google Drive' in the Destinations panel."
            )

        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        token_data = json.loads(self.tokens_file.read_text())
        creds = Credentials(
            token=token_data.get("access_token"),
            refresh_token=token_data["refresh_token"],
            token_uri=token_data["token_uri"],
            client_id=token_data["client_id"],
            client_secret=token_data["client_secret"],
            scopes=_SCOPES,
        )

        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token_data["access_token"] = creds.token
            self.tokens_file.write_text(json.dumps(token_data))
            self.tokens_file.chmod(0o600)

        self._service = build("drive", "v3", credentials=creds)
        return self._service

    def _get_or_create_folder(self, service: Any, name: str, parent_id: str) -> str:
        q = (
            f"name='{name}' and '{parent_id}' in parents "
            f"and mimeType='application/vnd.google-apps.folder' and trashed=false"
        )
        results = service.files().list(q=q, fields="files(id)").execute()
        files = results.get("files", [])
        if files:
            return files[0]["id"]
        folder = (
            service.files()
            .create(
                body={
                    "name": name,
                    "mimeType": "application/vnd.google-apps.folder",
                    "parents": [parent_id],
                },
                fields="id",
            )
            .execute()
        )
        return folder["id"]

    def _prune_old_files(self, service: Any, subfolder_id: str, keep_count: int, service_name: str) -> None:
        """Delete oldest files in subfolder beyond keep_count, oldest first."""
        if keep_count <= 0:
            return
        results = (
            service.files()
            .list(
                q=f"'{subfolder_id}' in parents and trashed=false",
                orderBy="createdTime",
                fields="files(id,name,createdTime)",
            )
            .execute()
        )
        files = results.get("files", [])
        excess = len(files) - keep_count
        if excess <= 0:
            return
        for f in files[:excess]:
            service.files().delete(fileId=f["id"]).execute()
            logger.info(f"[{service_name}] Pruned old Drive backup: {f['name']} (id={f['id']})")

    def upload(self, file_path: Path, result: BackupResult, keep_count: int = 0) -> None:
        """Upload a file into a per-service subfolder, then prune to keep_count."""
        from googleapiclient.http import MediaFileUpload

        if not file_path.exists():
            raise BackupError(f"File to upload does not exist: {file_path}")

        service = self._get_service()
        subfolder_id = self._get_or_create_folder(
            service, result.service_name, self.folder_id
        )
        file_metadata = {"name": file_path.name, "parents": [subfolder_id]}
        media = MediaFileUpload(str(file_path), resumable=True)
        uploaded = (
            service.files()
            .create(body=file_metadata, media_body=media, fields="id,name")
            .execute()
        )
        logger.info(
            f"[{result.service_name}] Uploaded to Google Drive: "
            f"{uploaded['name']} (id={uploaded['id']})"
        )
        self._prune_old_files(service, subfolder_id, keep_count, result.service_name)


ALL_DESTINATIONS: list[type[GoogleDriveDestination]] = [GoogleDriveDestination]


def create_google_drive_destination(
    config: dict, context_env: dict[str, str]
) -> GoogleDriveDestination | None:
    """Build a Google Drive destination if enabled, else return ``None``.

    The env var ``GOOGLE_DRIVE_ENABLED`` takes precedence over the config file;
    raises ``BackupError`` if enabled but the target folder ID is missing.
    """
    gd_config = config.get("destinations", {}).get("google_drive", {})

    match context_env.get(ENABLED_ENV, "").strip().lower():
        case "true":
            enabled = True
        case "false":
            enabled = False
        case _:
            enabled = gd_config.get("enabled", False)

    if not enabled:
        return None

    folder_id_env = gd_config.get("folder_id_env", FOLDER_ID_ENV)
    folder_id = context_env.get(folder_id_env) or context_env.get(FOLDER_ID_ENV)
    credentials_file = Path(gd_config.get("credentials_file", str(_CREDS_PATH)))
    tokens_file = Path(gd_config.get("tokens_file", str(_TOKENS_PATH)))

    if not folder_id:
        raise BackupError(
            f"Google Drive enabled but {FOLDER_ID_ENV} is not set. "
            "Configure it in the UI under Destinations."
        )
    return GoogleDriveDestination(
        credentials_file=credentials_file,
        tokens_file=tokens_file,
        folder_id=folder_id,
    )
