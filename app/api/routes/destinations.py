"""Routes for upload destinations: settings, connection tests, and account logins."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import json
import os
import secrets
import shutil
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.core.context import BackupError
from app.core.env_manager import EnvManager
from app.core.scheduler import BackupScheduler
from app.destinations import google_drive
from app.destinations.base import (
    BackupDestination,
    UnknownHostKeyError,
    state_dir,
    write_private,
)
from app.destinations.registry import ALL_DESTINATIONS, destination_class, is_enabled
from app.destinations.sftp import KEY_FILE_ENV, save_private_key

_MAX_LOGIN_SESSIONS = 20
_TEST_TIMEOUT_SECONDS = 90

# Bring-your-own Google client flow: state -> {redirect_uri, code_verifier}
_pending_oauth: dict[str, dict[str, str]] = {}
# Device-code logins in progress: id -> {"status": pending|connected|failed, "message": str}
_logins: dict[str, dict[str, str]] = {}
# Strong references so running login tasks are not garbage-collected.
_login_tasks: set[asyncio.Task] = set()


class DestinationEnvVarUpdate(BaseModel):
    """Request body carrying environment variable updates for a destination."""

    updates: dict[str, str]


class CredentialsPayload(BaseModel):
    """Request body wrapping the raw OAuth2 client secret JSON."""

    json_content: str


class KeyPayload(BaseModel):
    """Request body carrying a pasted SSH private key."""

    key: str


def create_router(env_manager: EnvManager, scheduler: BackupScheduler) -> APIRouter:
    """Return a router exposing destination configuration, tests and logins."""
    router = APIRouter()

    def read_env() -> dict[str, str]:
        # Same merge as the scheduler: process env, overridden by the live .env file.
        return {**os.environ, **env_manager.read()}

    def get_class(dest_type: str) -> type[BackupDestination]:
        dest_class = destination_class(dest_type)
        if dest_class is None:
            raise HTTPException(status_code=404, detail=f"Unknown destination '{dest_type}'")
        return dest_class

    def describe(dest_class: type[BackupDestination], env: dict[str, str]) -> dict[str, Any]:
        file_env = env_manager.read()
        info: dict[str, Any] = {
            "type": dest_class.destination_type,
            "display_name": dest_class.display_name,
            "description": dest_class.description,
            "enabled_key": dest_class.enabled_key(),
            "enabled": is_enabled(dest_class, scheduler.get_config(), env),
            "login_provider": dest_class.login_provider,
            "login_available": dest_class.login_available(env),
            "login": dest_class.login_status(env),
            "env_vars": [
                spec.describe(file_env.get(spec.key, "")) for spec in dest_class.env_var_specs
            ],
        }
        if dest_class is google_drive.GoogleDriveDestination:
            info["credentials_uploaded"] = google_drive.credentials_path().is_file()
        return info

    @router.get("/destinations")
    async def list_destinations() -> list[dict[str, Any]]:
        """List destinations with their settings (secrets masked) and login status."""

        def build() -> list[dict[str, Any]]:
            env = read_env()
            return [describe(d, env) for d in ALL_DESTINATIONS]

        return await asyncio.to_thread(build)

    @router.put("/destinations/{dest_type}/env-vars")
    async def update_destination_env_vars(dest_type: str, body: DestinationEnvVarUpdate) -> dict:
        """Persist settings for a destination after validating the keys."""
        dest_class = get_class(dest_type)
        allowed = {spec.key for spec in dest_class.env_var_specs} | {dest_class.enabled_key()}
        bad_keys = set(body.updates) - allowed
        if bad_keys:
            raise HTTPException(
                status_code=400, detail=f"Unknown settings for '{dest_type}': {bad_keys}"
            )
        await asyncio.to_thread(env_manager.update, body.updates)
        return {"status": "saved", "updated_keys": list(body.updates)}

    @router.post("/destinations/{dest_type}/test")
    async def test_destination(dest_type: str) -> dict[str, Any]:
        """Connect with the saved settings and report what happened."""
        dest_class = get_class(dest_type)

        def run() -> dict[str, Any]:
            destination = None
            try:
                destination = dest_class.from_env(read_env())
                return {"ok": True, "message": destination.check()}
            except UnknownHostKeyError as e:
                return {"ok": False, "message": str(e), "fingerprint": e.fingerprint}
            except BackupError as e:
                return {"ok": False, "message": str(e)}
            except Exception as e:  # noqa: BLE001 - report any library error to the user
                return {"ok": False, "message": f"{type(e).__name__}: {e}"}
            finally:
                if destination is not None:
                    destination.close()

        try:
            return await asyncio.wait_for(asyncio.to_thread(run), _TEST_TIMEOUT_SECONDS)
        except TimeoutError:
            return {"ok": False, "message": f"No answer within {_TEST_TIMEOUT_SECONDS} seconds."}

    @router.post("/destinations/{dest_type}/login")
    async def start_login(dest_type: str) -> dict[str, Any]:
        """Start a device-code login; the GUI shows the code and polls the status."""
        dest_class = get_class(dest_type)
        try:
            login = await asyncio.to_thread(dest_class.start_login, read_env())
        except BackupError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        login_id = secrets.token_urlsafe(16)
        _logins[login_id] = {"status": "pending", "message": ""}
        while len(_logins) > _MAX_LOGIN_SESSIONS:
            _logins.pop(next(iter(_logins)))

        async def finish() -> None:
            try:
                account = await asyncio.to_thread(login.wait)
                _logins[login_id] = {"status": "connected", "message": account}
            except Exception as e:  # noqa: BLE001 - any failure ends the login with a message
                _logins[login_id] = {"status": "failed", "message": str(e)}

        task = asyncio.create_task(finish())
        _login_tasks.add(task)
        task.add_done_callback(_login_tasks.discard)
        return {
            "id": login_id,
            "user_code": login.user_code,
            "verification_url": login.verification_url,
            "expires_in": login.expires_in,
        }

    @router.get("/destinations/logins/{login_id}")
    async def login_progress(login_id: str) -> dict[str, str]:
        """Report whether a device-code login has finished."""
        if login_id not in _logins:
            raise HTTPException(status_code=404, detail="Unknown or expired login")
        return _logins[login_id]

    @router.delete("/destinations/{dest_type}/login")
    async def disconnect(dest_type: str) -> dict:
        """Revoke (where the provider allows it) and forget the stored tokens."""
        dest_class = get_class(dest_type)
        try:
            await asyncio.to_thread(dest_class.disconnect, read_env())
        except BackupError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"status": "disconnected"}

    @router.post("/destinations/sftp/key")
    async def save_sftp_key(body: KeyPayload) -> dict:
        """Store a pasted SSH private key (mode 600) and point SFTP_KEY_FILE at it."""

        def run() -> str:
            env = read_env()
            path = save_private_key(env, body.key, state_dir(env))
            env_manager.update({KEY_FILE_ENV: str(path)})
            return str(path)

        try:
            path = await asyncio.to_thread(run)
        except BackupError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"status": "saved", "path": path}

    # ── Google Drive with your own OAuth client (advanced) ─────────────────────

    @router.post("/destinations/google_drive/credentials")
    async def upload_credentials(body: CredentialsPayload) -> dict:
        """Validate and store the uploaded Google OAuth2 client secret JSON."""
        try:
            parsed = json.loads(body.json_content)
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}") from e

        client_info = parsed.get("web") or parsed.get("installed")
        if not client_info:
            raise HTTPException(
                status_code=400,
                detail="Invalid client credentials JSON. Expected an OAuth2 client secret "
                "(must have 'web' or 'installed' key, not a service account).",
            )
        missing = {"client_id", "client_secret", "token_uri"} - set(client_info)
        if missing:
            raise HTTPException(
                status_code=400, detail=f"Client credentials JSON missing fields: {missing}"
            )

        def store() -> None:
            path = google_drive.credentials_path()
            if path.is_dir():
                shutil.rmtree(path)
            write_private(path, body.json_content)

        await asyncio.to_thread(store)
        return {"status": "saved", "client_id": client_info.get("client_id")}

    @router.post("/destinations/google_drive/oauth/start")
    async def oauth_start(body: dict) -> dict:
        """Build a Google OAuth2 authorization URL using PKCE and return it."""
        creds_path = google_drive.credentials_path()
        if not creds_path.is_file():
            raise HTTPException(status_code=400, detail="Upload client_secret.json first")

        redirect_base = str(body.get("redirect_base", "")).rstrip("/")
        if not redirect_base:
            raise HTTPException(status_code=400, detail="redirect_base is required")
        redirect_uri = f"{redirect_base}/api/destinations/google_drive/oauth/callback"

        try:
            from google_auth_oauthlib.flow import Flow

            flow = Flow.from_client_secrets_file(
                str(creds_path), scopes=google_drive._SCOPES, redirect_uri=redirect_uri
            )
            code_verifier = secrets.token_urlsafe(48)
            code_challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
                .rstrip(b"=")
                .decode()
            )
            auth_url, state = flow.authorization_url(
                access_type="offline",
                prompt="consent",
                code_challenge=code_challenge,
                code_challenge_method="S256",
            )
        except Exception as e:  # noqa: BLE001 - surface any Google flow error to the UI
            raise HTTPException(status_code=500, detail=f"Failed to build auth URL: {e}") from e

        _pending_oauth[state] = {"redirect_uri": redirect_uri, "code_verifier": code_verifier}
        return {"auth_url": auth_url}

    @router.get("/destinations/google_drive/oauth/callback")
    async def oauth_callback(code: str, state: str) -> HTMLResponse:
        """Exchange the OAuth2 authorization code for tokens and store them."""
        pending = _pending_oauth.pop(state, None)
        if not pending:
            return HTMLResponse(
                "<html><body><h2>Error: unknown or expired OAuth state. "
                "Try authorizing again.</h2></body></html>",
                status_code=400,
            )

        def exchange() -> None:
            from google_auth_oauthlib.flow import Flow

            flow = Flow.from_client_secrets_file(
                str(google_drive.credentials_path()),
                scopes=google_drive._SCOPES,
                redirect_uri=pending["redirect_uri"],
            )
            flow.fetch_token(code=code, code_verifier=pending["code_verifier"])
            creds = flow.credentials
            tokens = {
                "refresh_token": creds.refresh_token,
                "token_uri": creds.token_uri,
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "account": google_drive._account_email(creds.token),
            }
            write_private(google_drive.tokens_path(), json.dumps(tokens))

        try:
            await asyncio.to_thread(exchange)
        except Exception as e:  # noqa: BLE001 - report any token-exchange error to the user
            return HTMLResponse(
                f"<html><body><h2>Authorization failed: {html.escape(str(e))}</h2></body></html>",
                status_code=500,
            )
        return HTMLResponse(
            "<html><body><h2>&#10003; Google Drive connected.</h2>"
            "<p>You can close this tab and return to Homelab Takeout.</p></body></html>"
        )

    return router
