"""Worker exporting Manyfold library metadata (not the model files) via its v0 REST API."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

import requests

from app.core.context import BackupContext, BackupError, BackupResult
from app.workers.base import BackupWorker, EnvVarSpec, fetch_json


class ManyfoldWorker(BackupWorker):
    """Export creators, collections and models (tags, notes, licence, links, file list)."""

    worker_type: ClassVar[str] = "manyfold"
    display_name: ClassVar[str] = "Manyfold"
    description: ClassVar[str] = (
        "Exports creators, collections and every model's name, notes, tags, licence, links, "
        "collections and file list; the 3D files themselves, libraries and lists are not included."
    )
    env_var_specs: ClassVar[list[EnvVarSpec]] = [
        EnvVarSpec(
            key="MANYFOLD_URL",
            label="Manyfold URL",
            description="Base URL of your Manyfold instance (e.g. https://manyfold.example.com).",
            secret=False,
            required=True,
        ),
        EnvVarSpec(
            key="MANYFOLD_CLIENT_ID",
            label="Client ID",
            description=(
                "Manyfold user menu → Developer → API keys → New API key: scope 'read', "
                "redirect URI urn:ietf:wg:oauth:2.0:oob, Confidential ticked. "
                "Copy the client ID from the key's page."
            ),
            secret=False,
            required=True,
        ),
        EnvVarSpec(
            key="MANYFOLD_CLIENT_SECRET",
            label="Client Secret",
            description="The client secret shown on the same API key page.",
            secret=True,
            required=True,
        ),
    ]

    def run(self, context: BackupContext) -> BackupResult:
        started_at = datetime.now()
        base = self.require_env(context, "MANYFOLD_URL").rstrip("/")
        client_id = self.require_env(context, "MANYFOLD_CLIENT_ID")
        secret = self.require_env(context, "MANYFOLD_CLIENT_SECRET")

        with requests.Session() as s:
            try:
                token = fetch_json(
                    s,
                    "POST",
                    f"{base}/oauth/token",
                    data={
                        "grant_type": "client_credentials",
                        "client_id": client_id,
                        "client_secret": secret,
                        "scope": "read",
                    },
                )["access_token"]
            except (BackupError, KeyError) as e:
                raise BackupError(
                    "Manyfold did not issue an access token: check MANYFOLD_URL, the client "
                    f"ID/secret, and that the API key has the 'read' scope ({e})"
                ) from e
            s.headers["Authorization"] = f"Bearer {token}"
            s.headers["Accept"] = "application/vnd.manyfold.v0+json"

            def details(kind: str) -> list:
                """Page through /<kind> and fetch every member's full record."""
                out: list = []
                page = 1
                while page:
                    data = fetch_json(s, "GET", f"{base}/{kind}", params={"page": str(page)})
                    for member in data["member"]:
                        # @id may carry the server's public hostname; use only its id.
                        item_id = member["@id"].rstrip("/").rsplit("/", 1)[-1]
                        out.append(fetch_json(s, "GET", f"{base}/{kind}/{item_id}"))
                    page = page + 1 if data.get("view", {}).get("next") else 0
                return out

            files = {
                "creators": details("creators"),
                "collections": details("collections"),
                "models": details("models"),
            }
        return self.archive_json(context, started_at, files)
