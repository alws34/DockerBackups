"""Worker exporting Bar Assistant data via its REST API into a tar.gz archive."""

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

# Endpoints that require ?bar_id=<id>
_BAR_ENDPOINTS = [
    "cocktails",
    "ingredients",
    "glasses",
    "tags",
    "utensils",
    "cocktail-methods",
    "collections",
]

# Endpoints that do NOT require bar_id
_GLOBAL_ENDPOINTS = [
    "bars",
]


class BarAssistantWorker(BackupWorker):
    """Export cocktails, ingredients, glasses, and more via the Bar Assistant API."""

    worker_type: ClassVar[str] = "bar_assistant"
    display_name: ClassVar[str] = "Bar Assistant"
    description: ClassVar[str] = (
        "Exports cocktails, ingredients, glasses, tags and more "
        "via Bar Assistant REST API."
    )
    env_var_specs: ClassVar[list[EnvVarSpec]] = [
        EnvVarSpec(
            key="BAR_ASSISTANT_URL",
            label="Bar Assistant URL",
            description=(
                "Base URL including path prefix. "
                "For Salt Rim reverse-proxy setups: https://yourdomain.tld/bar — "
                "for direct API container access: http://bar-assistant:3000"
            ),
            secret=False,
            required=True,
        ),
        EnvVarSpec(
            key="BAR_ASSISTANT_API_KEY",
            label="API Key",
            description="Bar Assistant API token (from user profile → API tokens).",
            secret=True,
            required=True,
        ),
    ]

    def run(self, context: BackupContext) -> BackupResult:
        """Export each bar's data to JSON and bundle it into a tar.gz archive."""
        started_at = datetime.now()

        base_url = context.env.get("BAR_ASSISTANT_URL", "").rstrip("/")
        if not base_url:
            raise BackupError("BAR_ASSISTANT_URL is not set")
        api_key = context.env.get("BAR_ASSISTANT_API_KEY", "").strip()
        if not api_key:
            raise BackupError("BAR_ASSISTANT_API_KEY is not set")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }

        def get(
            path: str,
            params: dict | None = None,
            extra_headers: dict | None = None,
        ) -> dict | list:
            url = f"{base_url}/api/{path}"
            h = {**headers, **(extra_headers or {})}
            resp = requests.get(url, headers=h, params=params or {}, timeout=60)
            if resp.status_code == 401:
                raise BackupError("BAR_ASSISTANT_API_KEY is invalid or expired (401)")
            resp.raise_for_status()
            try:
                return resp.json()
            except ValueError as e:
                raise BackupError(
                    f"Non-JSON response from {url} (status {resp.status_code}): "
                    f"{resp.text[:300]!r}"
                ) from e

        # Fetch bars to get bar ID(s)
        try:
            bars_resp = get("bars")
        except requests.RequestException as e:
            raise BackupError(f"Failed to fetch bars: {e}") from e

        bars = bars_resp.get("data", bars_resp) if isinstance(bars_resp, dict) else bars_resp
        if not bars:
            raise BackupError("No bars found — cannot determine bar_id")

        backup_dir = self.service_backup_dir(context)
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = started_at.strftime("%Y%m%d_%H%M%S")
        total_records = 0

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)

            # Save bars metadata
            (work / "bars.json").write_text(json.dumps(bars, indent=2, ensure_ascii=False))

            for bar in bars:
                bar_id = bar.get("id")
                bar_slug = bar.get("slug") or str(bar_id)
                bar_dir = work / f"bar_{bar_slug}"
                bar_dir.mkdir()
                logger.info(f"bar_assistant: exporting bar {bar_slug!r} (id={bar_id})")

                bar_headers = {**headers, "Bar-Assistant-Bar-Id": str(bar_id)}
                for endpoint in _BAR_ENDPOINTS:
                    try:
                        data = get(endpoint, params={"per_page": 1000}, extra_headers=bar_headers)
                        records = data.get("data", data) if isinstance(data, dict) else data
                        out = bar_dir / f"{endpoint.replace('-', '_')}.json"
                        out.write_text(json.dumps(records, indent=2, ensure_ascii=False))
                        count = len(records) if isinstance(records, list) else "?"
                        total_records += count if isinstance(count, int) else 0
                        logger.info(f"bar_assistant: {endpoint}: {count} records")
                    except requests.RequestException as e:
                        logger.warning(f"bar_assistant: skipping {endpoint}: {e}")

            archive = backup_dir / f"bar_assistant_{timestamp}.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                tar.add(work, arcname=f"bar_assistant_{timestamp}")

        archive.chmod(0o600)
        self.cleanup_old_files(backup_dir, "bar_assistant_*.tar.gz", context.retention_days)

        return BackupResult(
            service_name=self.service_name,
            worker_type=self.worker_type,
            success=True,
            message=(
                f"{total_records} records exported: {archive.name} "
                f"({archive.stat().st_size} bytes)"
            ),
            output_files=[archive],
            started_at=started_at,
            finished_at=datetime.now(),
        )
