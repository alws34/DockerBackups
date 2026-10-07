"""OneDrive destination: "Login with Microsoft" via MSAL, files in the app's own folder."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import ClassVar
from urllib.parse import quote

import msal
import requests

from app.core.context import BackupError
from app.destinations import oauth_apps
from app.destinations.base import BackupDestination, DeviceLogin, state_dir, write_private
from app.workers.base import EnvVarSpec

logger = logging.getLogger(__name__)

# AppFolder: the app can only touch Apps/<app name>/ in the user's OneDrive.
_SCOPES = ["Files.ReadWrite.AppFolder"]
_AUTHORITY = "https://login.microsoftonline.com/common"
_APPROOT = "https://graph.microsoft.com/v1.0/me/drive/special/approot"
# Upload sessions need chunks in multiples of 320 KiB.
_CHUNK = 320 * 1024 * 10

CLIENT_ID_ENV = "MICROSOFT_CLIENT_ID"

# MSAL rotates the refresh token on every use; never let two threads refresh at once.
_cache_lock = threading.Lock()


def _client_id(env: dict[str, str]) -> str:
    return env.get(CLIENT_ID_ENV, "").strip() or oauth_apps.MICROSOFT_CLIENT_ID


def _cache_path(env: dict[str, str]) -> Path:
    return state_dir(env) / "onedrive-token-cache.json"


def _app(env: dict[str, str]) -> tuple[msal.PublicClientApplication, msal.SerializableTokenCache]:
    client_id = _client_id(env)
    if not client_id:
        raise BackupError(
            "This build has no built-in Microsoft app. Enter your own client ID under Advanced."
        )
    cache = msal.SerializableTokenCache()
    path = _cache_path(env)
    if path.is_file():
        cache.deserialize(path.read_text())
    app = msal.PublicClientApplication(client_id, authority=_AUTHORITY, token_cache=cache)
    return app, cache


def _save(env: dict[str, str], cache: msal.SerializableTokenCache) -> None:
    if cache.has_state_changed:
        write_private(_cache_path(env), cache.serialize())


class OneDriveDestination(BackupDestination):
    """Upload each backup into ``Apps/Homelab Takeout/<service>/`` on OneDrive."""

    destination_type: ClassVar[str] = "onedrive"
    display_name: ClassVar[str] = "OneDrive"
    description: ClassVar[str] = (
        "Log in with Microsoft and backups go to Apps/Homelab Takeout in your OneDrive. "
        "The app can't see anything else. Personal accounts; work/school if your org allows it."
    )
    login_provider: ClassVar[str | None] = "microsoft"
    env_var_specs: ClassVar[list[EnvVarSpec]] = [
        EnvVarSpec(
            CLIENT_ID_ENV,
            "Your own Microsoft client ID",
            "Optional. An Entra app registration with public client flows enabled.",
            False,
            False,
            advanced=True,
        ),
    ]

    def __init__(self, env: dict[str, str]) -> None:
        self.env = env

    @classmethod
    def login_available(cls, env: dict[str, str]) -> bool:
        return bool(_client_id(env))

    @classmethod
    def login_status(cls, env: dict[str, str]) -> dict[str, object]:
        if not _client_id(env) or not _cache_path(env).is_file():
            return {"connected": False, "account": ""}
        app, _ = _app(env)
        accounts = app.get_accounts()
        return {
            "connected": bool(accounts),
            "account": accounts[0].get("username", "") if accounts else "",
        }

    @classmethod
    def start_login(cls, env: dict[str, str]) -> DeviceLogin:
        app, cache = _app(env)
        flow = app.initiate_device_flow(scopes=_SCOPES)
        if "user_code" not in flow:
            raise BackupError(flow.get("error_description", "Could not start Microsoft login."))

        def wait() -> str:
            result = app.acquire_token_by_device_flow(flow)  # blocks until done or expired
            if "access_token" not in result:
                raise BackupError(result.get("error_description", "Microsoft login failed."))
            with _cache_lock:
                _save(env, cache)
            return result.get("id_token_claims", {}).get("preferred_username", "")

        return DeviceLogin(
            user_code=flow["user_code"],
            verification_url=flow["verification_uri"],
            expires_in=int(flow.get("expires_in", 900)),
            wait=wait,
        )

    @classmethod
    def disconnect(cls, env: dict[str, str]) -> None:
        # Public clients can't revoke refresh tokens server-side; users can remove the
        # app's access at https://account.live.com/consent/Manage.
        _cache_path(env).unlink(missing_ok=True)

    @classmethod
    def from_env(cls, env: dict[str, str]) -> OneDriveDestination:
        if not cls.login_status(env)["connected"]:
            raise BackupError("OneDrive is not connected. Click 'Log in with Microsoft'.")
        return cls(env)

    def _token(self) -> str:
        with _cache_lock:
            app, cache = _app(self.env)
            accounts = app.get_accounts()
            result = app.acquire_token_silent(_SCOPES, account=accounts[0]) if accounts else None
            _save(self.env, cache)
        if not result or "access_token" not in result:
            raise BackupError("Microsoft login expired or was revoked. Log in again.")
        return result["access_token"]

    def _graph(self, method: str, url: str, **kwargs: object) -> requests.Response:
        headers = {"Authorization": f"Bearer {self._token()}", **kwargs.pop("headers", {})}
        try:
            resp = requests.request(method, url, headers=headers, timeout=120, **kwargs)
        except requests.RequestException as e:
            raise BackupError(f"OneDrive request failed: {e}") from e
        if resp.status_code >= 400 and resp.status_code != 404:
            raise BackupError(f"OneDrive error {resp.status_code}: {resp.text[:200]}")
        return resp

    def _item(self, *parts: str) -> str:
        return f"{_APPROOT}:/" + "/".join(quote(p, safe="") for p in parts) + ":"

    def put(self, file_path: Path, service_name: str) -> None:
        item = self._item(service_name, file_path.name)
        size = file_path.stat().st_size
        if size == 0:  # upload sessions reject empty files
            self._graph("PUT", f"{item}/content", data=b"")
            return
        session = self._graph(
            "POST",
            f"{item}/createUploadSession",
            json={"item": {"@microsoft.graph.conflictBehavior": "replace"}},
        ).json()
        upload_url = session["uploadUrl"]
        with file_path.open("rb") as f:
            offset = 0
            while offset < size:
                chunk = f.read(_CHUNK)
                end = offset + len(chunk) - 1
                # The pre-authenticated upload URL must NOT get an Authorization header.
                resp = requests.put(
                    upload_url,
                    data=chunk,
                    headers={"Content-Range": f"bytes {offset}-{end}/{size}"},
                    timeout=300,
                )
                if resp.status_code >= 400:
                    raise BackupError(
                        f"OneDrive upload failed {resp.status_code}: {resp.text[:200]}"
                    )
                offset += len(chunk)

    def list_names(self, service_name: str) -> list[str]:
        url: str | None = f"{self._item(service_name)}/children?$select=name&$top=999"
        names: list[str] = []
        while url:
            resp = self._graph("GET", url)
            if resp.status_code == 404:
                return []
            body = resp.json()
            names += [item["name"] for item in body.get("value", [])]
            url = body.get("@odata.nextLink")
        return names

    def remove(self, service_name: str, name: str) -> None:
        self._graph("DELETE", self._item(service_name, name))

    def check(self) -> str:
        self._graph("GET", _APPROOT)
        account = self.login_status(self.env)["account"] or "your Microsoft account"
        return f"Connected as {account}; files go to Apps/Homelab Takeout."
