"""Worker exporting n8n workflows, tags, and variables via the n8n REST API."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import ClassVar

import requests

from app.core.context import BackupContext, BackupError, BackupResult
from app.workers.base import BackupWorker, EnvVarSpec

logger = logging.getLogger(__name__)


class N8nWorker(BackupWorker):
    """Export all n8n workflows, tags, and variables to a single JSON archive."""

    worker_type: ClassVar[str] = "n8n"
    display_name: ClassVar[str] = "n8n"
    description: ClassVar[str] = (
        "Full export of n8n workflows, tags, and variables via the n8n REST API."
    )
    env_var_specs: ClassVar[list[EnvVarSpec]] = [
        EnvVarSpec(
            key="N8N_URL",
            label="n8n URL",
            description="Base URL of your n8n instance (e.g. https://n8n.example.com).",
            secret=False,
            required=True,
        ),
        EnvVarSpec(
            key="N8N_API_KEY",
            label="API Key",
            description=(
                "n8n API key — Settings → n8n API → Create an API key."
            ),
            secret=True,
            required=True,
        ),
    ]

    def run(self, context: BackupContext) -> BackupResult:
        started_at = datetime.now()

        base_url = context.env.get("N8N_URL", "").rstrip("/")
        if not base_url:
            raise BackupError("N8N_URL is not set")
        api_key = context.env.get("N8N_API_KEY")
        if not api_key:
            raise BackupError("N8N_API_KEY is not set")

        headers = {"X-N8N-API-KEY": api_key, "Accept": "application/json"}

        workflows = self._fetch_all(f"{base_url}/api/v1/workflows", headers)
        tags = self._fetch_all(f"{base_url}/api/v1/tags", headers)

        # Variables endpoint was added in n8n v1.0 — skip gracefully if absent.
        try:
            variables = self._fetch_all(f"{base_url}/api/v1/variables", headers)
        except BackupError as exc:
            logger.warning(f"n8n: skipping variables — {exc}")
            variables = []

        backup = {
            "exported_at": started_at.isoformat(),
            "n8n_url": base_url,
            "workflows": workflows,
            "tags": tags,
            "variables": variables,
        }

        backup_dir = self.service_backup_dir(context)
        backup_dir.mkdir(parents=True, exist_ok=True)

        timestamp = started_at.strftime("%Y%m%d_%H%M%S")
        output_file = backup_dir / f"n8n_{timestamp}.json"
        output_file.write_text(json.dumps(backup, indent=2, ensure_ascii=False))
        output_file.chmod(0o600)

        logger.info(
            f"n8n: {len(workflows)} workflows, {len(tags)} tags, "
            f"{len(variables)} variables"
        )

        self.cleanup_old_files(backup_dir, "n8n_*.json", context.retention_days)

        return BackupResult(
            service_name=self.service_name,
            worker_type=self.worker_type,
            success=True,
            message=(
                f"{len(workflows)} workflows, {len(tags)} tags → "
                f"{output_file.name} ({output_file.stat().st_size} bytes)"
            ),
            output_files=[output_file],
            started_at=started_at,
            finished_at=datetime.now(),
        )

    def _fetch_all(self, url: str, headers: dict) -> list:
        """Collect every page of a cursor-paginated n8n API endpoint."""
        items: list = []
        cursor: str | None = None
        while True:
            params = {"cursor": cursor} if cursor else {}
            try:
                resp = requests.get(url, headers=headers, params=params, timeout=60)
                resp.raise_for_status()
                data = resp.json()
            except requests.RequestException as exc:
                raise BackupError(f"Failed to fetch {url}: {exc}") from exc
            items.extend(data.get("data", []))
            cursor = data.get("nextCursor")
            if not cursor:
                break
        return items
