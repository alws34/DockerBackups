"""Worker exporting Immich library metadata (not the media files) via its REST API."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

import requests

from app.core.context import BackupContext, BackupResult
from app.workers.base import BackupWorker, EnvVarSpec, fetch_json

_PAGE_SIZE = 1000


class ImmichWorker(BackupWorker):
    """Export albums, album membership, people, tags and asset metadata.

    Photos/videos themselves are NOT exported — this captures the organisation
    layer that would be lost if only the raw library folder were restored.
    """

    worker_type: ClassVar[str] = "immich"
    display_name: ClassVar[str] = "Immich (metadata)"
    description: ClassVar[str] = (
        "Exports albums + their asset IDs, people, tags, stacks, memories, shared links "
        "and per-asset metadata (EXIF, faces, favourites). Media files are not included."
    )
    env_var_specs: ClassVar[list[EnvVarSpec]] = [
        EnvVarSpec(
            key="IMMICH_URL",
            label="Immich URL",
            description="Base URL of your Immich instance (e.g. https://immich.example.com).",
            secret=False,
            required=True,
        ),
        EnvVarSpec(
            key="IMMICH_API_KEY",
            label="API Key",
            description=(
                "Immich Account Settings → API Keys → New API Key (read permissions). "
                "Exports only what this user can see."
            ),
            secret=True,
            required=True,
        ),
    ]

    def run(self, context: BackupContext) -> BackupResult:
        started_at = datetime.now()
        api = f"{self.require_env(context, 'IMMICH_URL').rstrip('/')}/api"
        key = self.require_env(context, "IMMICH_API_KEY")

        with requests.Session() as s:
            s.headers["x-api-key"] = key

            def get(path: str, **params: str) -> dict | list:
                return fetch_json(s, "GET", f"{api}{path}", params=params)

            def search_assets(**body: object) -> list:
                items: list = []
                page = 1
                while page:
                    data = fetch_json(
                        s,
                        "POST",
                        f"{api}/search/metadata",
                        json={**body, "page": page, "size": _PAGE_SIZE},
                    )["assets"]
                    items.extend(data["items"])
                    page = int(data["nextPage"]) if data.get("nextPage") else 0
                return items

            people: list = []
            page = 1
            while True:
                data = get("/people", page=str(page), size=str(_PAGE_SIZE), withHidden="true")
                people.extend(data["people"])
                if not data.get("hasNextPage"):
                    break
                page += 1

            albums = get("/albums")
            files = {
                "users": get("/users"),
                "albums": albums,
                "album_assets": {
                    a["id"]: [x["id"] for x in search_assets(albumIds=[a["id"]])] for a in albums
                },
                "people": people,
                "tags": get("/tags"),
                "stacks": get("/stacks"),
                "memories": get("/memories"),
                "shared_links": get("/shared-links"),
                "assets": search_assets(withExif=True, withPeople=True),
            }
        return self.archive_json(context, started_at, files)
