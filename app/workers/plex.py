"""Worker exporting Plex libraries, watch state, playlists and history via the server API."""

from __future__ import annotations

import re
from datetime import datetime
from typing import ClassVar

import requests

from app.core.context import BackupContext, BackupError, BackupResult
from app.workers.base import BackupWorker, EnvVarSpec, fetch_json

_PAGE_SIZE = 500

# Plex item types to list per library type. Watch state of TV lives on the
# episodes (4), not the shows (2); music ratings sit on artists, albums and tracks.
_ITEM_TYPES = {"movie": [1], "show": [2, 4], "artist": [8, 9, 10], "photo": [13, 14]}
_COLLECTION_TYPE = 18

# Server preferences that hold credentials; never written to the backup.
_SECRET_PREF = re.compile(r"token|passw|secret|certificatekey", re.IGNORECASE)


class PlexWorker(BackupWorker):
    """Export what a Plex database loss would take with it, through the local server API."""

    worker_type: ClassVar[str] = "plex"
    display_name: ClassVar[str] = "Plex"
    description: ClassVar[str] = (
        "Exports libraries and their settings, every item's watch state, ratings and match "
        "IDs, collections, playlists, watch history and server settings; media files, artwork "
        "and other users' watch state are not included."
    )
    env_var_specs: ClassVar[list[EnvVarSpec]] = [
        EnvVarSpec(
            key="PLEX_URL",
            label="Plex URL",
            description="Base URL of your Plex Media Server (e.g. http://plex.lan:32400).",
            secret=False,
            required=True,
        ),
        EnvVarSpec(
            key="PLEX_TOKEN",
            label="X-Plex-Token",
            description=(
                "Signed in as the server owner in Plex Web: open any item → ⋯ → Get Info → "
                "View XML, and copy the X-Plex-Token value from the address bar "
                "(support.plex.tv: 'Finding an authentication token')."
            ),
            secret=True,
            required=True,
        ),
    ]

    def run(self, context: BackupContext) -> BackupResult:
        started_at = datetime.now()
        base = self.require_env(context, "PLEX_URL").rstrip("/")
        token = self.require_env(context, "PLEX_TOKEN")

        with requests.Session() as s:
            s.headers.update({"X-Plex-Token": token, "Accept": "application/json"})

            def get(path: str, **params: object) -> dict:
                return fetch_json(s, "GET", f"{base}{path}", params=params)["MediaContainer"]

            def paged(path: str, **params: object) -> list:
                items: list = []
                while True:
                    mc = fetch_json(
                        s,
                        "GET",
                        f"{base}{path}",
                        params=params,
                        headers={
                            "X-Plex-Container-Start": str(len(items)),
                            "X-Plex-Container-Size": str(_PAGE_SIZE),
                        },
                    )["MediaContainer"]
                    page = mc.get("Metadata", [])
                    items.extend(page)
                    total = int(mc.get("totalSize", 0))
                    # A short page is the last one; an oversized one means paging was ignored.
                    if len(page) != _PAGE_SIZE or (total and len(items) >= total):
                        return items

            try:
                server = get("/")
            except BackupError as e:
                if "401" in str(e):
                    raise BackupError(
                        "Plex rejected PLEX_TOKEN (401 Unauthorized). Copy a fresh "
                        "X-Plex-Token of the server owner account."
                    ) from e
                raise

            sections = get("/library/sections").get("Directory", [])
            items: dict[str, list] = {}
            collections: dict[str, list] = {}
            for sec in sections:
                key = sec["key"]
                path = f"/library/sections/{key}/all"
                items[key] = [
                    item
                    for t in _ITEM_TYPES.get(sec["type"], [None])
                    for item in paged(path, type=t, includeGuids=1)
                ]
                collections[key] = paged(path, type=_COLLECTION_TYPE)

            playlists = paged("/playlists")
            prefs = get("/:/prefs").get("Setting", [])
            files = {
                "server": server,
                "sections": sections,
                "section_settings": {
                    sec["key"]: get(f"/library/sections/{sec['key']}/prefs").get("Setting", [])
                    for sec in sections
                },
                "items": items,
                "collections": collections,
                "collection_items": {
                    c["ratingKey"]: paged(f"/library/collections/{c['ratingKey']}/children")
                    for cs in collections.values()
                    for c in cs
                },
                "playlists": playlists,
                "playlist_items": {
                    p["ratingKey"]: paged(f"/playlists/{p['ratingKey']}/items") for p in playlists
                },
                "history": paged("/status/sessions/history/all", sort="viewedAt:desc"),
                "accounts": get("/accounts").get("Account", []),
                "prefs": [
                    p for p in prefs if not p.get("secure") and not _SECRET_PREF.search(p["id"])
                ],
            }
        return self.archive_json(context, started_at, files)
