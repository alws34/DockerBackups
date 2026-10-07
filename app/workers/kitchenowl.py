"""Worker exporting KitchenOwl households and recipes via its API."""

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


class KitchenOwlWorker(BackupWorker):
    """Export recipes and household data via the KitchenOwl API."""

    worker_type: ClassVar[str] = "kitchenowl"
    display_name: ClassVar[str] = "KitchenOwl"
    description: ClassVar[str] = (
        "Exports recipes and household data via KitchenOwl API using a long-lived token."
    )
    env_var_specs: ClassVar[list[EnvVarSpec]] = [
        EnvVarSpec(
            key="KITCHENOWL_URL",
            label="KitchenOwl URL",
            description="Base URL of your KitchenOwl instance (e.g. http://kitchenowl:80).",
            secret=False,
            required=True,
        ),
        EnvVarSpec(
            key="KITCHENOWL_TOKEN",
            label="Long-Lived Token",
            description=(
                "Long-lived access token. Generate via KitchenOwl Settings → Account → "
                "Long-Lived Tokens, or call POST /api/auth/llt with a logged-in session."
            ),
            secret=True,
            required=True,
        ),
    ]

    def run(self, context: BackupContext) -> BackupResult:
        """Export every household's recipes, items, and lists into a tar.gz."""
        started_at = datetime.now()

        base_url = context.env.get("KITCHENOWL_URL", "").rstrip("/")
        if not base_url:
            raise BackupError("KITCHENOWL_URL is not set")
        token = context.env.get("KITCHENOWL_TOKEN")
        if not token:
            raise BackupError("KITCHENOWL_TOKEN is not set")

        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)

            try:
                households = _get(base_url, headers, "/api/household")
            except requests.RequestException as e:
                raise BackupError(f"Failed to fetch households: {e}") from e

            if not isinstance(households, list):
                households = [households]

            (work / "households.json").write_text(
                json.dumps(households, indent=2, ensure_ascii=False)
            )
            for hh in households:
                _export_household(base_url, headers, hh, work)
            return self.archive_dir(context, started_at, work, "API export")


# File name -> endpoint under /api/household/<id>/
_SECTIONS = {"recipes": "recipe", "items": "item", "shoppinglists": "shoppinglist"}


def _get(base_url: str, headers: dict, path: str) -> dict | list:
    resp = requests.get(f"{base_url}{path}", headers=headers, timeout=60)
    resp.raise_for_status()
    return resp.json()


def _export_household(base_url: str, headers: dict, hh: dict, work: Path) -> None:
    """Write one household's recipes, items and shopping lists into ``household_<id>/``."""
    hh_id = hh.get("household", {}).get("id") or hh.get("id")
    if not hh_id:
        return
    hh_dir = work / f"household_{hh_id}"
    hh_dir.mkdir()
    for name, endpoint in _SECTIONS.items():
        try:
            data = _get(base_url, headers, f"/api/household/{hh_id}/{endpoint}")
        except requests.RequestException as e:
            logger.warning(f"kitchenowl: could not fetch {name} for household {hh_id}: {e}")
            continue
        (hh_dir / f"{name}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False))
        if name == "recipes":
            count = len(data) if isinstance(data, list) else "?"
            logger.info(f"kitchenowl: household {hh_id}: {count} recipes")
