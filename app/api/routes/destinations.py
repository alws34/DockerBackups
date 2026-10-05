"""Routes for managing backup destinations and the Google Drive OAuth2 flow."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.core.env_manager import EnvManager
from app.destinations.google_drive import (
    _CREDS_PATH,
    _SCOPES,
    _TOKENS_PATH,
    ALL_DESTINATIONS,
    ENABLED_ENV,
)

# In-memory store: state -> {redirect_uri, code_verifier}
_pending_oauth: dict[str, dict[str, str]] = {}


class DestinationEnvVarUpdate(BaseModel):
    """Request body carrying environment variable updates for a destination."""

    updates: dict[str, str]


class CredentialsPayload(BaseModel):
    """Request body wrapping the raw OAuth2 client secret JSON."""

    json_content: str


def create_router(env_manager: EnvManager) -> APIRouter:
    """Return a router exposing destination configuration and OAuth2 endpoints."""
    router = APIRouter()

    @router.get("/destinations")
    async def list_destinations() -> list[dict[str, Any]]:
        """List configured destinations with their env var and auth status."""
        env_values = env_manager.read()
        result = []
        for dest_class in ALL_DESTINATIONS:
            env_var_info = [
                spec.describe(env_values.get(spec.key, "")) for spec in dest_class.env_var_specs
            ]
            enabled_raw = env_values.get(ENABLED_ENV, "").strip().lower()
            creds_path = Path(os.environ.get("GOOGLE_CREDENTIALS_FILE", str(_CREDS_PATH)))
            tokens_path = Path(os.environ.get("GOOGLE_TOKENS_FILE", str(_TOKENS_PATH)))
            result.append(
                {
                    "type": dest_class.destination_type,
                    "display_name": dest_class.display_name,
                    "description": dest_class.description,
                    "enabled": enabled_raw == "true",
                    "credentials_uploaded": creds_path.exists() and creds_path.is_file(),
                    "authorized": tokens_path.exists() and tokens_path.is_file(),
                    "env_vars": env_var_info,
                }
            )
        return result

    @router.put("/destinations/{dest_type}/env-vars")
    async def update_destination_env_vars(dest_type: str, body: DestinationEnvVarUpdate) -> dict:
        """Persist env var updates for a destination after validating the keys."""
        dest_class = next((d for d in ALL_DESTINATIONS if d.destination_type == dest_type), None)
        if dest_class is None:
            raise HTTPException(status_code=404, detail=f"Unknown destination type '{dest_type}'")
        allowed_keys = {spec.key for spec in dest_class.env_var_specs}
        bad_keys = set(body.updates) - allowed_keys
        if bad_keys:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown env var keys for '{dest_type}': {bad_keys}",
            )
        env_manager.update(body.updates)
        return {"status": "saved", "updated_keys": list(body.updates)}

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
        required = {"client_id", "client_secret", "token_uri"}
        missing = required - set(client_info)
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"Client credentials JSON missing fields: {missing}",
            )

        creds_path = Path(os.environ.get("GOOGLE_CREDENTIALS_FILE", str(_CREDS_PATH)))
        creds_path.parent.mkdir(parents=True, exist_ok=True)
        if creds_path.exists() and creds_path.is_dir():
            shutil.rmtree(creds_path)
        creds_path.write_text(body.json_content)
        creds_path.chmod(0o600)
        return {"status": "saved", "client_id": client_info.get("client_id")}

    @router.post("/destinations/google_drive/oauth/start")
    async def oauth_start(request: Request, body: dict) -> dict:
        """Build a Google OAuth2 authorization URL using PKCE and return it."""
        creds_path = Path(os.environ.get("GOOGLE_CREDENTIALS_FILE", str(_CREDS_PATH)))
        if not creds_path.exists() or not creds_path.is_file():
            raise HTTPException(status_code=400, detail="Upload client_secret.json first")

        redirect_base = str(body.get("redirect_base", "")).rstrip("/")
        if not redirect_base:
            raise HTTPException(status_code=400, detail="redirect_base is required")
        redirect_uri = f"{redirect_base}/api/destinations/google_drive/oauth/callback"

        try:
            from google_auth_oauthlib.flow import Flow

            flow = Flow.from_client_secrets_file(
                str(creds_path), scopes=_SCOPES, redirect_uri=redirect_uri
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

        _pending_oauth[state] = {
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        }
        return {"auth_url": auth_url}

    @router.get("/destinations/google_drive/oauth/callback")
    async def oauth_callback(code: str, state: str) -> HTMLResponse:
        """Exchange the OAuth2 authorization code for tokens and store them."""
        pending = _pending_oauth.pop(state, None)
        if not pending or not pending.get("redirect_uri"):
            return HTMLResponse(
                "<html><body><h2>Error: unknown or expired OAuth state. "
                "Try authorizing again.</h2></body></html>",
                status_code=400,
            )
        redirect_uri = pending["redirect_uri"]

        creds_path = Path(os.environ.get("GOOGLE_CREDENTIALS_FILE", str(_CREDS_PATH)))
        tokens_path = Path(os.environ.get("GOOGLE_TOKENS_FILE", str(_TOKENS_PATH)))

        try:
            from google_auth_oauthlib.flow import Flow

            flow = Flow.from_client_secrets_file(
                str(creds_path), scopes=_SCOPES, redirect_uri=redirect_uri
            )
            flow.fetch_token(code=code, code_verifier=pending["code_verifier"])
            creds = flow.credentials
        except Exception as e:  # noqa: BLE001 - report any token-exchange error to the user
            return HTMLResponse(
                f"<html><body><h2>Authorization failed: {e}</h2></body></html>",
                status_code=500,
            )

        tokens = {
            "access_token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
        }
        tokens_path.parent.mkdir(parents=True, exist_ok=True)
        tokens_path.write_text(json.dumps(tokens))
        tokens_path.chmod(0o600)

        return HTMLResponse(
            "<html><body style='font-family:sans-serif;text-align:center;padding:3rem'>"
            "<h2>&#10003; Google Drive authorized successfully!</h2>"
            "<p>You can close this tab and return to Homelab Takeout.</p>"
            "<script>setTimeout(()=>window.close(),2000)</script>"
            "</body></html>"
        )

    @router.delete("/destinations/google_drive/oauth")
    async def oauth_revoke() -> dict:
        """Delete the stored Google Drive tokens, revoking authorization."""
        tokens_path = Path(os.environ.get("GOOGLE_TOKENS_FILE", str(_TOKENS_PATH)))
        if tokens_path.exists():
            tokens_path.unlink()
        return {"status": "revoked"}

    return router
