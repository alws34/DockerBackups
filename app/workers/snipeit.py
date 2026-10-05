"""Worker exporting Snipe-IT assets and configuration via its REST API."""

from __future__ import annotations

import json
import logging
import tarfile
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

        def fetch_all(endpoint: str) -> list:
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
                total = data.get("total", len(records))
                offset += len(rows)
                if not rows or offset >= total:
                    break
            return records

        backup_dir = self.service_backup_dir(context)
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = started_at.strftime("%Y%m%d_%H%M%S")
        total_records = 0
        failed: list[str] = []

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            for endpoint in _ENDPOINTS:
                try:
                    records = fetch_all(endpoint)
                    out = work / f"{endpoint}.json"
                    out.write_text(json.dumps(records, indent=2, ensure_ascii=False))
                    logger.info(f"snipeit: {endpoint}: {len(records)} records")
                    total_records += len(records)
                except requests.HTTPError as e:
                    if e.response is not None and e.response.status_code == 401:
                        raise BackupError(
                            "SNIPEIT_API_KEY is invalid or expired (401 Unauthorized)"
                        ) from e
                    logger.warning(f"snipeit: skipping {endpoint}: {e}")
                    (work / f"{endpoint}.json").write_text("[]")
                    failed.append(endpoint)
                except requests.RequestException as e:
                    logger.warning(f"snipeit: skipping {endpoint}: {e}")
                    (work / f"{endpoint}.json").write_text("[]")
                    failed.append(endpoint)

            if len(failed) == len(_ENDPOINTS):
                raise BackupError(f"All Snipe-IT endpoints failed; check SNIPEIT_URL ({base_url})")

            archive = backup_dir / f"{self.service_name}_{timestamp}.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                tar.add(work, arcname=f"{self.service_name}_{timestamp}")

        archive.chmod(0o600)
        self.cleanup_old_files(backup_dir, f"{self.service_name}_*.tar.gz", context.retention_days)

        return BackupResult(
            service_name=self.service_name,
            worker_type=self.worker_type,
            success=True,
            message=(
                f"{total_records} records exported: {archive.name} ({archive.stat().st_size} bytes)"
            ),
            output_files=[archive],
            started_at=started_at,
            finished_at=datetime.now(),
        )
