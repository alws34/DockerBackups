"""Worker exporting Jellyfin users, watch state, playlists, collections and settings via REST."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

import requests

from app.core.context import BackupContext, BackupResult
from app.workers.base import BackupWorker, EnvVarSpec, fetch_json

_PAGE_SIZE = 1000
# Enough for a restore to match items again by provider IDs (IMDb/TMDb/TVDb) or file path.
_FIELDS = "ProviderIds,Path"
# Each filter is its own query: Jellyfin ANDs multiple filters together.
_WATCH_FILTERS = ("IsPlayed", "IsFavorite", "IsResumable", "Likes", "Dislikes")
_SECRET_WORDS = ("password", "secret", "token", "apikey")


def _redact(data: object) -> object:
    """Drop any key that looks like a credential, at any depth."""
    if isinstance(data, dict):
        return {
            k: _redact(v) for k, v in data.items() if not any(w in k.lower() for w in _SECRET_WORDS)
        }
    if isinstance(data, list):
        return [_redact(v) for v in data]
    return data


class JellyfinWorker(BackupWorker):
    """Export the server-side data a Jellyfin database loss would take with it."""

    worker_type: ClassVar[str] = "jellyfin"
    display_name: ClassVar[str] = "Jellyfin"
    description: ClassVar[str] = (
        "Exports users and their policies, per-user watch state and favourites, playlists, "
        "collections, library definitions, server settings and the plugin list; "
        "media files and metadata images are not included."
    )
    env_var_specs: ClassVar[list[EnvVarSpec]] = [
        EnvVarSpec(
            key="JELLYFIN_URL",
            label="Jellyfin URL",
            description="Base URL of your Jellyfin server (e.g. https://jellyfin.example.com).",
            secret=False,
            required=True,
        ),
        EnvVarSpec(
            key="JELLYFIN_API_KEY",
            label="API Key",
            description=(
                "Jellyfin Dashboard → API Keys → + (give it a name like 'backup'). "
                "API keys have admin rights, which the export needs to read every user."
            ),
            secret=True,
            required=True,
        ),
    ]

    def run(self, context: BackupContext) -> BackupResult:
        started_at = datetime.now()
        base = self.require_env(context, "JELLYFIN_URL").rstrip("/")
        key = self.require_env(context, "JELLYFIN_API_KEY")

        with requests.Session() as s:
            s.headers["Authorization"] = f'MediaBrowser Token="{key}"'

            def get(path: str, **params: str) -> dict | list:
                return fetch_json(s, "GET", f"{base}{path}", params=params)

            def paged(path: str, **params: str) -> list:
                items: list = []
                while True:
                    page = get(
                        path,
                        **params,
                        fields=_FIELDS,
                        enableImages="false",
                        startIndex=str(len(items)),
                        limit=str(_PAGE_SIZE),
                    )
                    items.extend(page["Items"])
                    if len(page["Items"]) < _PAGE_SIZE:
                        return items

            users = get("/Users")
            watch_state: dict[str, list] = {}
            containers: dict[str, tuple[dict, str]] = {}  # item id -> (item, a user who sees it)
            for user in users:
                uid = user["Id"]
                seen: dict[str, dict] = {}
                for f in _WATCH_FILTERS:
                    for item in paged("/Items", userId=uid, recursive="true", filters=f):
                        seen[item["Id"]] = item
                watch_state[user["Name"]] = list(seen.values())
                for item in paged(
                    "/Items", userId=uid, recursive="true", includeItemTypes="Playlist,BoxSet"
                ):
                    containers.setdefault(item["Id"], (item, uid))

            playlists, collections = [], []
            for cid, (item, uid) in containers.items():
                if item.get("Type") == "Playlist":
                    playlists.append(
                        {
                            "playlist": item,
                            "sharing": get(f"/Playlists/{cid}"),
                            "items": paged(f"/Playlists/{cid}/Items", userId=uid),
                        }
                    )
                else:
                    collections.append(
                        {"collection": item, "items": paged("/Items", userId=uid, parentId=cid)}
                    )

            files = {
                "users": users,
                "watch_state": watch_state,
                "playlists": playlists,
                "collections": collections,
                "libraries": get("/Library/VirtualFolders"),
                "server_configuration": _redact(
                    {
                        "system_info": get("/System/Info"),
                        "system": get("/System/Configuration"),
                        "network": get("/System/Configuration/network"),
                        "encoding": get("/System/Configuration/encoding"),
                        "branding": get("/System/Configuration/Branding"),
                    }
                ),
                "plugins": get("/Plugins"),
            }
        return self.archive_json(context, started_at, files)
