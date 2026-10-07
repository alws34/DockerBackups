"""Worker exporting Bar Assistant data via its REST API into a tar.gz archive."""

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
        "Exports cocktails, ingredients, glasses, tags and more via Bar Assistant REST API."
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

        # Fetch bars to get bar ID(s)
        try:
            bars_resp = _get(base_url, headers, "bars")
        except requests.RequestException as e:
            raise BackupError(f"Failed to fetch bars: {e}") from e

        bars = _unwrap(bars_resp)
        if not bars:
            raise BackupError("No bars found — cannot determine bar_id")

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "bars.json").write_text(json.dumps(bars, indent=2, ensure_ascii=False))
            total_records = sum(_export_bar(base_url, headers, bar, work) for bar in bars)
            return self.archive_dir(context, started_at, work, f"{total_records} records exported")


def _get(base_url: str, headers: dict, path: str, params: dict | None = None) -> dict | list:
    url = f"{base_url}/api/{path}"
    resp = requests.get(url, headers=headers, params=params or {}, timeout=60)
    if resp.status_code == 401:
        raise BackupError("BAR_ASSISTANT_API_KEY is invalid or expired (401)")
    resp.raise_for_status()
    try:
        return resp.json()
    except ValueError as e:
        raise BackupError(
            f"Non-JSON response from {url} (status {resp.status_code}): {resp.text[:300]!r}"
        ) from e


def _unwrap(body: dict | list) -> object:
    """Bar Assistant wraps lists as ``{"data": [...]}``; return the payload."""
    return body.get("data", body) if isinstance(body, dict) else body


def _export_bar(base_url: str, headers: dict, bar: dict, work: Path) -> int:
    """Export one bar into ``bar_<slug>/``; return how many records were written."""
    bar_id = bar.get("id")
    bar_slug = bar.get("slug") or str(bar_id)
    bar_dir = work / f"bar_{bar_slug}"
    bar_dir.mkdir()
    logger.info(f"bar_assistant: exporting bar {bar_slug!r} (id={bar_id})")
    bar_headers = {**headers, "Bar-Assistant-Bar-Id": str(bar_id)}
    return sum(_export_endpoint(base_url, bar_headers, ep, bar_dir) for ep in _BAR_ENDPOINTS)


def _export_endpoint(base_url: str, headers: dict, endpoint: str, bar_dir: Path) -> int:
    """Write one endpoint of a bar to JSON; return its record count (0 if skipped)."""
    try:
        records = _unwrap(_get(base_url, headers, endpoint, params={"per_page": 1000}))
    except requests.RequestException as e:
        logger.warning(f"bar_assistant: skipping {endpoint}: {e}")
        return 0
    out = bar_dir / f"{endpoint.replace('-', '_')}.json"
    out.write_text(json.dumps(records, indent=2, ensure_ascii=False))
    if not isinstance(records, list):
        logger.info(f"bar_assistant: {endpoint}: ? records")
        return 0
    logger.info(f"bar_assistant: {endpoint}: {len(records)} records")
    return len(records)
