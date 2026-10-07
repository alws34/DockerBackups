"""Worker exporting Snipe-IT assets and configuration via its REST API."""

from __future__ import annotations

import json
import logging
import tempfile
from datetime import datetime
from pathlib import Path
from typing import ClassVar

import requests

from app.core.context import BackupContext, BackupError, BackupResult
from app.workers.base import BackupWorker, EnvVarSpec

logger = logging.getLogger(__name__)

_ENDPOINTS = [
    "hardware",
    "licenses",
    "accessories",
    "consumables",
    "components",
    "users",
    "locations",
    "manufacturers",
    "categories",
    "companies",
    "departments",
    "statuslabels",
    "suppliers",
    "fields",
]

_PAGE_SIZE = 500


class SnipeItWorker(BackupWorker):
    """Export assets, licenses, and configuration via the Snipe-IT REST API."""

    worker_type: ClassVar[str] = "snipeit"
    display_name: ClassVar[str] = "Snipe-IT"
    description: ClassVar[str] = (
        "Full export of assets, licenses, and configuration via Snipe-IT REST API."
    )
    env_var_specs: ClassVar[list[EnvVarSpec]] = [
        EnvVarSpec(
            key="SNIPEIT_URL",
            label="Snipe-IT URL",
            description="Base URL of your Snipe-IT instance (e.g. http://snipeit:80).",
            secret=False,
            required=True,
        ),
        EnvVarSpec(
            key="SNIPEIT_API_KEY",
            label="API Key",
            description="API token from Snipe-IT Settings → API → Create Token.",
            secret=True,
            required=True,
        ),
    ]

    def run(self, context: BackupContext) -> BackupResult:
        """Page through each endpoint, write JSON files, and archive them."""
        started_at = datetime.now()

        base_url = context.env.get("SNIPEIT_URL", "").rstrip("/")
        if not base_url:
            raise BackupError("SNIPEIT_URL is not set")
        api_key = context.env.get("SNIPEIT_API_KEY", "").strip()
        if not api_key:
            raise BackupError("SNIPEIT_API_KEY is not set")
        if api_key.lower().startswith("bearer "):
            api_key = api_key[7:].strip()

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            counts = [_export(base_url, headers, endpoint, work) for endpoint in _ENDPOINTS]
            if all(count is None for count in counts):
                raise BackupError(f"All Snipe-IT endpoints failed; check SNIPEIT_URL ({base_url})")
            total_records = sum(count or 0 for count in counts)
            return self.archive_dir(context, started_at, work, f"{total_records} records exported")


def _fetch_all(base_url: str, headers: dict, endpoint: str) -> list:
    """Return every record of one endpoint, following Snipe-IT's offset pagination."""
    records: list = []
    offset = 0
    while True:
        resp = requests.get(
            f"{base_url}/api/v1/{endpoint}",
            headers=headers,
            params={"limit": _PAGE_SIZE, "offset": offset},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        if offset == 0:
            keys = list(data.keys()) if isinstance(data, dict) else type(data).__name__
            logger.info(f"snipeit: {endpoint} response keys: {keys}")
        # Snipe-IT uses "rows" at the top level.
        if isinstance(data, list):
            return data
        rows = data.get("rows") or data.get("data") or []
        records.extend(rows)
        offset += len(rows)
        if not rows or offset >= data.get("total", len(records)):
            return records


def _export(base_url: str, headers: dict, endpoint: str, work: Path) -> int | None:
    """Write one endpoint to ``<endpoint>.json``; return its record count, None if skipped."""
    out = work / f"{endpoint}.json"
    try:
        records = _fetch_all(base_url, headers, endpoint)
    except requests.RequestException as e:
        if isinstance(e, requests.HTTPError) and _unauthorized(e):
            raise BackupError("SNIPEIT_API_KEY is invalid or expired (401 Unauthorized)") from e
        logger.warning(f"snipeit: skipping {endpoint}: {e}")
        out.write_text("[]")
        return None
    out.write_text(json.dumps(records, indent=2, ensure_ascii=False))
    logger.info(f"snipeit: {endpoint}: {len(records)} records")
    return len(records)


def _unauthorized(error: requests.HTTPError) -> bool:
    return error.response is not None and error.response.status_code == 401
