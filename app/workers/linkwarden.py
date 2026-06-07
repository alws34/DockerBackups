"""Worker exporting Linkwarden bookmarks and collections via its API."""

from __future__ import annotations

import json
import logging
from datetime import datetime

import requests

from app.core.context import BackupContext, BackupError, BackupResult
from app.workers.base import BackupWorker, EnvVarSpec

logger = logging.getLogger(__name__)


class LinkwardenWorker(BackupWorker):
    """Export bookmarks and collections via the Linkwarden migration endpoint."""

    worker_type: str = "linkwarden"
    display_name: str = "Linkwarden"
    description: str = (
        "Full export of bookmarks and collections "
        "via Linkwarden API migration endpoint."
    )
    env_var_specs: list[EnvVarSpec] = [
        EnvVarSpec(
            key="LINKWARDEN_URL",
            label="Linkwarden URL",
            description=(
                "Base URL of your Linkwarden instance (e.g. http://linkwarden:3000)."
            ),
            secret=False,
            required=True,
        ),
        EnvVarSpec(
            key="LINKWARDEN_ACCESS_TOKEN",
            label="Access Token",
            description=(
                "Access token from Linkwarden Settings → "
                "Access Tokens → New Access Token."
            ),
            secret=True,
            required=True,
        ),
    ]

    def run(self, context: BackupContext) -> BackupResult:
        """Fetch the full migration export and write it as a single JSON file."""
        started_at = datetime.now()

        base_url = context.env.get("LINKWARDEN_URL", "").rstrip("/")
        if not base_url:
            raise BackupError("LINKWARDEN_URL is not set")
        token = context.env.get("LINKWARDEN_ACCESS_TOKEN")
        if not token:
            raise BackupError("LINKWARDEN_ACCESS_TOKEN is not set")

        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

        try:
            resp = requests.get(f"{base_url}/api/v1/migration", headers=headers, timeout=120)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            raise BackupError(f"Failed to fetch migration data: {e}") from e

        backup_dir = self.service_backup_dir(context)
        backup_dir.mkdir(parents=True, exist_ok=True)

        timestamp = started_at.strftime("%Y%m%d_%H%M%S")
        output_file = backup_dir / f"linkwarden_{timestamp}.json"
        output_file.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        output_file.chmod(0o600)

        response = data.get("response", {})
        links_count = len(data.get("links", response.get("links", [])))
        collections_count = len(data.get("collections", response.get("collections", [])))
        logger.info(f"linkwarden: {links_count} links, {collections_count} collections")

        self.cleanup_old_files(backup_dir, "linkwarden_*.json", context.retention_days)

        return BackupResult(
            service_name=self.service_name,
            worker_type=self.worker_type,
            success=True,
            message=(
                f"{links_count} links, {collections_count} collections → "
                f"{output_file.name} ({output_file.stat().st_size} bytes)"
            ),
            output_files=[output_file],
            started_at=started_at,
            finished_at=datetime.now(),
        )
