"""Google Drive destination: "Login with Google" (device flow) or your own OAuth client."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, ClassVar

import requests

from app.core.context import BackupError
from app.destinations import oauth_apps
from app.destinations.base import BackupDestination, DeviceLogin, write_private
from app.workers.base import EnvVarSpec

logger = logging.getLogger(__name__)

# drive.file: the app only ever sees files and folders it created itself.
_SCOPES = ["https://www.googleapis.com/auth/drive.file"]
_CREDS_PATH = Path("/config/google-credentials.json")
_TOKENS_PATH = Path("/config/google-tokens.json")
_DEVICE_CODE_URL = "https://oauth2.googleapis.com/device/code"
_TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - endpoint URL
_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
_ABOUT_URL = "https://www.googleapis.com/drive/v3/about"
_FOLDER_MIME = "application/vnd.google-apps.folder"
_DEFAULT_ROOT_NAME = "Homelab Takeout"

ENABLED_ENV = "GOOGLE_DRIVE_ENABLED"
FOLDER_ID_ENV = "GOOGLE_DRIVE_FOLDER_ID"
CLIENT_ID_ENV = "GOOGLE_DEVICE_CLIENT_ID"
CLIENT_SECRET_ENV = "GOOGLE_DEVICE_CLIENT_SECRET"  # noqa: S105 - env var name

_LOGIN_ERRORS = {
    "access_denied": "Access was denied on the Google page.",
    "expired_token": "The code expired before sign-in finished. Try again.",
    "invalid_client": "The Google OAuth client is not valid for device login.",
}


def tokens_path() -> Path:
    """Where the Google refresh token lives (shared by both login methods)."""
    return Path(os.environ.get("GOOGLE_TOKENS_FILE", str(_TOKENS_PATH)))


def credentials_path() -> Path:
    """Where a bring-your-own ``client_secret.json`` is stored."""
    return Path(os.environ.get("GOOGLE_CREDENTIALS_FILE", str(_CREDS_PATH)))


def _device_client(env: dict[str, str]) -> tuple[str, str]:
    """Return the device-flow client: the user's own if set, else the built-in one."""
    client_id = env.get(CLIENT_ID_ENV, "").strip() or oauth_apps.GOOGLE_CLIENT_ID
    secret = env.get(CLIENT_SECRET_ENV, "").strip() or oauth_apps.GOOGLE_CLIENT_SECRET
    return client_id, secret


def _quote(value: str) -> str:
    """Escape a value for a Drive search query string literal."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _account_email(access_token: str) -> str:
    """Best-effort email of the signed-in account, for display only."""
    try:
        resp = requests.get(
            _ABOUT_URL,
            params={"fields": "user(emailAddress)"},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30,
        )
        return resp.json().get("user", {}).get("emailAddress", "") if resp.ok else ""
    except (requests.RequestException, ValueError):
        return ""


def _poll_for_tokens(
    client_id: str, secret: str, device_code: str, interval: int, expires_in: int
) -> str:
    """Poll Google until the user approves the device code; store the tokens."""
    deadline = time.monotonic() + expires_in
    while time.monotonic() < deadline:
        time.sleep(interval)
        try:
            resp = requests.post(
                _TOKEN_URL,
                data={
                    "client_id": client_id,
                    "client_secret": secret,
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
                timeout=30,
            )
            body = resp.json()
        except (requests.RequestException, ValueError):
            continue  # transient network trouble: keep polling until the deadline
        if resp.ok and body.get("refresh_token"):
            account = _account_email(body.get("access_token", ""))
            write_private(
                tokens_path(),
                json.dumps(
                    {
                        "refresh_token": body["refresh_token"],
                        "token_uri": _TOKEN_URL,
                        "client_id": client_id,
                        "client_secret": secret,
                        "account": account,
                    }
                ),
            )
            return account or "your Google account"
        error = body.get("error", "")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += 5
            continue
        raise BackupError(_LOGIN_ERRORS.get(error, body.get("error_description") or error))
    raise BackupError(_LOGIN_ERRORS["expired_token"])


class GoogleDriveDestination(BackupDestination):
    """Upload each backup into ``<root folder>/<service>/`` on Google Drive."""

    destination_type: ClassVar[str] = "google_drive"
    display_name: ClassVar[str] = "Google Drive"
    description: ClassVar[str] = (
        "Log in with Google and backups go to a 'Homelab Takeout' folder in your Drive. "
        "The app can only see files it created."
    )
    login_provider: ClassVar[str | None] = "google"
    env_var_specs: ClassVar[list[EnvVarSpec]] = [
        EnvVarSpec(
            FOLDER_ID_ENV,
            "Drive folder ID",
            "Optional. Leave empty to use a 'Homelab Takeout' folder the app creates.",
            False,
            False,
            advanced=True,
        ),
        EnvVarSpec(
            CLIENT_ID_ENV,
            "Your own device-login client ID",
            "Optional. A Google OAuth client of type 'TVs and Limited Input devices'.",
            False,
            False,
            advanced=True,
        ),
        EnvVarSpec(
            CLIENT_SECRET_ENV,
            "Your own device-login client secret",
            "Goes with the client ID above.",
            True,
            False,
            advanced=True,
        ),
    ]

    def __init__(self, token_data: dict[str, str], folder_id: str) -> None:
        self.token_data = token_data
        self.folder_id = folder_id
        self._service: Any = None
        self._folders: dict[str, str] = {}
        self._ids: dict[tuple[str, str], str] = {}

    @classmethod
    def enabled_key(cls) -> str:
        return ENABLED_ENV

    @classmethod
    def login_available(cls, env: dict[str, str]) -> bool:
        return bool(_device_client(env)[0])

    @classmethod
    def login_status(cls, env: dict[str, str]) -> dict[str, object]:
        path = tokens_path()
        if not path.is_file():
            return {"connected": False, "account": ""}
        try:
            account = json.loads(path.read_text()).get("account", "")
        except (OSError, ValueError):
            account = ""
        return {"connected": True, "account": account}

    @classmethod
    def start_login(cls, env: dict[str, str]) -> DeviceLogin:
        client_id, secret = _device_client(env)
        if not client_id:
            raise BackupError(
                "This build has no built-in Google app. Enter your own device-login client "
                "under Advanced, or use your own OAuth client."
            )
        try:
            resp = requests.post(
                _DEVICE_CODE_URL, data={"client_id": client_id, "scope": _SCOPES[0]}, timeout=30
            )
            body = resp.json()
        except (requests.RequestException, ValueError) as e:
            raise BackupError(f"Could not reach Google: {e}") from e
        if not resp.ok:
            error = body.get("error", "")
            raise BackupError(_LOGIN_ERRORS.get(error, body.get("error_description") or error))
        return DeviceLogin(
            user_code=body["user_code"],
            verification_url=body["verification_url"],
            expires_in=int(body["expires_in"]),
            wait=lambda: _poll_for_tokens(
                client_id,
                secret,
                body["device_code"],
                int(body.get("interval", 5)),
                int(body["expires_in"]),
            ),
        )

    @classmethod
    def disconnect(cls, env: dict[str, str]) -> None:
        path = tokens_path()
        if not path.is_file():
            return
        try:
            refresh_token = json.loads(path.read_text()).get("refresh_token", "")
            # Revoke at Google too, so the token is dead even if a copy of the file exists.
            requests.post(_REVOKE_URL, data={"token": refresh_token}, timeout=30)
        except (OSError, ValueError) as e:  # OSError includes requests.RequestException
            logger.warning(f"Google token revocation failed (deleting it locally anyway): {e}")
        path.unlink(missing_ok=True)

    @classmethod
    def from_env(cls, env: dict[str, str]) -> GoogleDriveDestination:
        path = tokens_path()
        if not path.is_file():
            raise BackupError("Google Drive is not connected. Click 'Log in with Google'.")
        try:
            token_data = json.loads(path.read_text())
        except (OSError, ValueError) as e:
            raise BackupError(f"Google token file is unreadable: {e}") from e
        return cls(token_data, env.get(FOLDER_ID_ENV, "").strip())

    def _drive(self) -> Any:
        if self._service is not None:
            return self._service
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        # google-auth refreshes the short-lived access token by itself; Google does not
        # rotate refresh tokens, so nothing needs writing back.
        creds = Credentials(
            token=None,
            refresh_token=self.token_data["refresh_token"],
            token_uri=self.token_data.get("token_uri", _TOKEN_URL),
            client_id=self.token_data["client_id"],
            client_secret=self.token_data.get("client_secret"),
            scopes=_SCOPES,
        )
        self._service = build("drive", "v3", credentials=creds, cache_discovery=False)
        return self._service

    def _call(self, request: Any) -> Any:
        from google.auth.exceptions import RefreshError
        from googleapiclient.errors import HttpError

        try:
            return request.execute()
        except RefreshError as e:
            raise BackupError(
                "Google login expired or was revoked. Click 'Log in with Google' again."
            ) from e
        except HttpError as e:
            raise BackupError(f"Google Drive error: {e.reason}") from e

    def _find_or_create_folder(self, name: str, parent: str) -> str:
        files = self._call(
            self._drive()
            .files()
            .list(
                q=(
                    f"name='{_quote(name)}' and '{_quote(parent)}' in parents "
                    f"and mimeType='{_FOLDER_MIME}' and trashed=false"
                ),
                fields="files(id)",
            )
        ).get("files", [])
        if files:
            return files[0]["id"]
        body = {"name": name, "mimeType": _FOLDER_MIME, "parents": [parent]}
        return self._call(self._drive().files().create(body=body, fields="id"))["id"]

    def _root(self) -> str:
        if not self.folder_id:
            self.folder_id = self._find_or_create_folder(_DEFAULT_ROOT_NAME, "root")
        return self.folder_id

    def _service_folder(self, service_name: str) -> str:
        if service_name not in self._folders:
            self._folders[service_name] = self._find_or_create_folder(service_name, self._root())
        return self._folders[service_name]

    def put(self, file_path: Path, service_name: str) -> None:
        from googleapiclient.http import MediaFileUpload

        body = {"name": file_path.name, "parents": [self._service_folder(service_name)]}
        media = MediaFileUpload(str(file_path), resumable=True)
        self._call(self._drive().files().create(body=body, media_body=media, fields="id"))

    def list_names(self, service_name: str) -> list[str]:
        folder = self._service_folder(service_name)
        names: list[str] = []
        page_token = None
        while True:
            page = self._call(
                self._drive()
                .files()
                .list(
                    q=f"'{_quote(folder)}' in parents and trashed=false",
                    fields="nextPageToken, files(id, name)",
                    pageSize=1000,
                    pageToken=page_token,
                )
            )
            for f in page.get("files", []):
                self._ids[(service_name, f["name"])] = f["id"]
                names.append(f["name"])
            page_token = page.get("nextPageToken")
            if not page_token:
                return names

    def remove(self, service_name: str, name: str) -> None:
        file_id = self._ids.get((service_name, name))
        if file_id is None:
            self.list_names(service_name)
            file_id = self._ids.get((service_name, name))
        if file_id is not None:
            self._call(self._drive().files().delete(fileId=file_id))

    def check(self) -> str:
        about = self._call(self._drive().about().get(fields="user(emailAddress)"))
        self._root()
        return f"Connected as {about.get('user', {}).get('emailAddress', 'your Google account')}."
