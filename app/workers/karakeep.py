"""Worker exporting Karakeep bookmarks, lists, tags, and highlights via its REST API."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

import requests

from app.core.context import BackupContext, BackupResult
from app.workers.base import BackupWorker, EnvVarSpec, fetch_json


class KarakeepWorker(BackupWorker):
    """Export everything a Karakeep user owns via the Karakeep REST API."""

    worker_type: ClassVar[str] = "karakeep"
    display_name: ClassVar[str] = "Karakeep"
    description: ClassVar[str] = (
        "Exports bookmarks (with content), lists and their members, tags and highlights."
    )
    env_var_specs: ClassVar[list[EnvVarSpec]] = [
        EnvVarSpec(
            key="KARAKEEP_URL",
            label="Karakeep URL",
            description="Base URL of your Karakeep instance (e.g. http://192.168.0.2:5010).",
            secret=False,
            required=True,
        ),
        EnvVarSpec(
            key="KARAKEEP_API_KEY",
            label="API Key",
            description="Karakeep Settings → API Keys → New API Key.",
            secret=True,
            required=True,
        ),
    ]

    def run(self, context: BackupContext) -> BackupResult:
        started_at = datetime.now()
        api = f"{self.require_env(context, 'KARAKEEP_URL').rstrip('/')}/api/v1"
        key = self.require_env(context, "KARAKEEP_API_KEY")

        with requests.Session() as s:
            s.headers["Authorization"] = f"Bearer {key}"

            def paged(path: str, field: str, **params: str) -> list:
                items: list = []
                cursor = None
                while True:
                    query = {**params, "limit": "100", **({"cursor": cursor} if cursor else {})}
                    data = fetch_json(s, "GET", f"{api}{path}", params=query)
                    items.extend(data[field])
                    cursor = data.get("nextCursor")
                    if not cursor:
                        return items

            lists = fetch_json(s, "GET", f"{api}/lists")["lists"]
            files = {
                "bookmarks": paged("/bookmarks", "bookmarks", includeContent="true"),
                "lists": lists,
                "list_members": {
                    lst["id"]: [
                        b["id"]
                        for b in paged(
                            f"/lists/{lst['id']}/bookmarks", "bookmarks", includeContent="false"
                        )
                    ]
                    for lst in lists
                },
                "tags": paged("/tags", "tags"),
                "highlights": paged("/highlights", "highlights"),
            }
        return self.archive_json(context, started_at, files)
