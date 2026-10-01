"""Worker exporting Spoolman spools, filaments, vendors, and settings via its REST API."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

import requests

from app.core.context import BackupContext, BackupResult
from app.workers.base import BackupWorker, EnvVarSpec, fetch_json

# (output name, API path)
_ENDPOINTS = [
    ("spools", "spool?allow_archived=true"),
    ("filaments", "filament"),
    ("vendors", "vendor"),
    ("settings", "setting/"),
    ("fields_spool", "field/spool"),
    ("fields_filament", "field/filament"),
    ("fields_vendor", "field/vendor"),
]


class SpoolmanWorker(BackupWorker):
    """Export Spoolman inventory via the Spoolman REST API (no auth)."""

    worker_type: ClassVar[str] = "spoolman"
    display_name: ClassVar[str] = "Spoolman"
    description: ClassVar[str] = (
        "Exports spools (incl. archived), filaments, vendors, extra fields and settings."
    )
    env_var_specs: ClassVar[list[EnvVarSpec]] = [
        EnvVarSpec(
            key="SPOOLMAN_URL",
            label="Spoolman URL",
            description="Base URL of your Spoolman instance (e.g. https://spoolman.example.com).",
            secret=False,
            required=True,
        ),
    ]

    def run(self, context: BackupContext) -> BackupResult:
        started_at = datetime.now()
        api = f"{self.require_env(context, 'SPOOLMAN_URL').rstrip('/')}/api/v1"
        with requests.Session() as s:
            files = {name: fetch_json(s, "GET", f"{api}/{path}") for name, path in _ENDPOINTS}
        return self.archive_json(context, started_at, files)
