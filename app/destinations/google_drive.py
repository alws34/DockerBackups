from __future__ import annotations

import json
import logging
from pathlib import Path

from app.core.context import BackupError, BackupResult
from app.destinations.base import BackupDestination
from app.workers.base import EnvVarSpec

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/drive.file"]
_CREDS_PATH = Path("/config/google-credentials.json")
_TOKENS_PATH = Path("/config/google-tokens.json")


class GoogleDriveDestination(BackupDestination):
    destination_type = "google_drive"
    display_name = "Google Drive"
    description = (
        "Upload backup files to a Google Drive folder via OAuth2. "
        "Upload your client_secret.json then click Authorize in the UI."
    )
    env_var_specs = [
        EnvVarSpec(
            key="GOOGLE_DRIVE_ENABLED",
            label="Enable Google Drive",
            description="Set to 'true' to upload backups to Google Drive after each successful run.",
            secret=False,
            required=False,
        ),
        EnvVarSpec(
            key="GOOGLE_DRIVE_FOLDER_ID",
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
        self._service = None

    def _get_service(self):
        if self._service is not None:
            return self._service

        if not self.credentials_file.exists():
            raise BackupError(
                "Google client credentials not found. Upload client_secret.json in the Destinations panel."
            )
        if not self.tokens_file.exists():
            raise BackupError(
                "Google Drive not authorized. Click 'Authorize Google Drive' in the Destinations panel."
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

    def _get_or_create_folder(self, service, name: str, parent_id: str) -> str:
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
                body={"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]},
                fields="id",
            )
            .execute()
        )
        return folder["id"]

    def upload(self, file_path: Path, result: BackupResult) -> None:
        from googleapiclient.http import MediaFileUpload

        if not file_path.exists():
            raise BackupError(f"File to upload does not exist: {file_path}")

        service = self._get_service()
        subfolder_id = self._get_or_create_folder(service, result.service_name, self.folder_id)
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


ALL_DESTINATIONS: list[type[GoogleDriveDestination]] = [GoogleDriveDestination]


def create_google_drive_destination(
    config: dict, context_env: dict
) -> GoogleDriveDestination | None:
    enabled_env = context_env.get("GOOGLE_DRIVE_ENABLED", "").strip().lower()
    if enabled_env == "true":
        enabled = True
    elif enabled_env == "false":
        enabled = False
    else:
        gd_config = config.get("destinations", {}).get("google_drive", {})
        enabled = gd_config.get("enabled", False)

    if not enabled:
        return None

    gd_config = config.get("destinations", {}).get("google_drive", {})
    folder_id_env = gd_config.get("folder_id_env", "GOOGLE_DRIVE_FOLDER_ID")
    folder_id = context_env.get(folder_id_env) or context_env.get("GOOGLE_DRIVE_FOLDER_ID")
    credentials_file = Path(gd_config.get("credentials_file", str(_CREDS_PATH)))
    tokens_file = Path(gd_config.get("tokens_file", str(_TOKENS_PATH)))

    if not folder_id:
        raise BackupError(
            "Google Drive enabled but GOOGLE_DRIVE_FOLDER_ID is not set. "
            "Configure it in the UI under Destinations."
        )
    return GoogleDriveDestination(
        credentials_file=credentials_file,
        tokens_file=tokens_file,
        folder_id=folder_id,
    )
