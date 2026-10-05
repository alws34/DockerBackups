"""Worker exporting OpenProject projects and their work via the API v3 (HAL+JSON)."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar
from urllib.parse import urljoin

import requests

from app.core.context import BackupContext, BackupError, BackupResult
from app.workers.base import BackupWorker, EnvVarSpec, fetch_json

# The server caps this at its "maximum API page size" setting; the paging loop copes with that.
_PAGE_SIZE = 1000


class OpenProjectWorker(BackupWorker):
    """Export projects, work packages and everything they reference, keeping HAL ``_links``."""

    worker_type: ClassVar[str] = "openproject"
    display_name: ClassVar[str] = "OpenProject"
    description: ClassVar[str] = (
        "Exports projects, work packages (open and closed) with relations, versions, "
        "categories, statuses, types, priorities, time entries, memberships, users and groups, "
        "saved queries, news and the attachment list; attached files and wiki pages are "
        "not included."
    )
    env_var_specs: ClassVar[list[EnvVarSpec]] = [
        EnvVarSpec(
            key="OPENPROJECT_URL",
            label="OpenProject URL",
            description="Base URL of your OpenProject instance (e.g. https://openproject.example.com).",
            secret=False,
            required=True,
        ),
        EnvVarSpec(
            key="OPENPROJECT_API_TOKEN",
            label="API Token",
            description=(
                "OpenProject avatar menu → My account → Access tokens → API → Generate. "
                "Exports only what this user can see; use an admin account for a full backup."
            ),
            secret=True,
            required=True,
        ),
    ]

    def run(self, context: BackupContext) -> BackupResult:
        started_at = datetime.now()
        base = self.require_env(context, "OPENPROJECT_URL").rstrip("/") + "/"
        token = self.require_env(context, "OPENPROJECT_API_TOKEN")

        with requests.Session() as s:
            s.auth = ("apikey", token)

            def collect(href: str, **params: str) -> list:
                # HAL hrefs are absolute paths ("/api/v3/..."), so urljoin keeps a sub-path prefix.
                url = urljoin(base, href)
                items: list = []
                page = 1  # OpenProject's "offset" is a page number, not an item offset.
                while True:
                    query = {**params, "offset": str(page), "pageSize": str(_PAGE_SIZE)}
                    data = fetch_json(s, "GET", url, params=query)
                    batch = data["_embedded"]["elements"]
                    items.extend(batch)
                    if not batch or len(items) >= data.get("total", 0):
                        return items
                    page += 1

            try:
                projects = collect("api/v3/projects")
            except BackupError as e:
                if "401" in str(e):
                    raise BackupError(
                        "OpenProject rejected OPENPROJECT_API_TOKEN (401). Generate a new token "
                        "under My account → Access tokens → API."
                    ) from e
                raise

            skipped: dict[str, str] = {}

            def optional(name: str, href: str) -> list:
                # Sections the token may not be allowed to see: skip them instead of
                # failing the whole backup, and say so in skipped.json.
                try:
                    return collect(href)
                except BackupError as e:
                    if "403" not in str(e):
                        raise
                    skipped[name] = "not allowed for this token (403)"
                    return []

            # filters=[] overrides the default "open status only" filter, so closed ones come too.
            work_packages = collect("api/v3/work_packages", filters="[]")
            files = {
                "projects": projects,
                "work_packages": work_packages,
                "relations": collect("api/v3/relations"),
                "versions": collect("api/v3/versions"),
                "categories": {
                    p["id"]: collect(p["_links"]["categories"]["href"])
                    for p in projects
                    if "categories" in p["_links"]
                },
                "statuses": collect("api/v3/statuses"),
                "types": collect("api/v3/types"),
                "priorities": collect("api/v3/priorities"),
                "time_entries": optional("time_entries", "api/v3/time_entries"),
                "memberships": optional("memberships", "api/v3/memberships"),
                "principals": optional("principals", "api/v3/principals"),
                "queries": optional("queries", "api/v3/queries"),
                "news": optional("news", "api/v3/news"),
                # ponytail: one request per work package; fine for homelab sizes.
                "attachments": {
                    wp["id"]: collect(wp["_links"]["attachments"]["href"])
                    for wp in work_packages
                    if "attachments" in wp["_links"]
                },
            }
            files["skipped"] = skipped
        return self.archive_json(context, started_at, files)
