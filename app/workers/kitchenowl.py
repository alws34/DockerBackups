"""Worker exporting KitchenOwl households and recipes via its API."""

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

        def get(path: str) -> dict | list:
            resp = requests.get(f"{base_url}{path}", headers=headers, timeout=60)
            resp.raise_for_status()
            return resp.json()

        backup_dir = self.service_backup_dir(context)
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = started_at.strftime("%Y%m%d_%H%M%S")

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)

            try:
                households = get("/api/household")
            except requests.RequestException as e:
                raise BackupError(f"Failed to fetch households: {e}") from e

            if not isinstance(households, list):
                households = [households]

            (work / "households.json").write_text(
                json.dumps(households, indent=2, ensure_ascii=False)
            )

            for hh in households:
                hh_id = hh.get("household", {}).get("id") or hh.get("id")
                if not hh_id:
                    continue
                hh_dir = work / f"household_{hh_id}"
                hh_dir.mkdir()

                # Recipes
                try:
                    recipes = get(f"/api/household/{hh_id}/recipe")
                    (hh_dir / "recipes.json").write_text(
                        json.dumps(recipes, indent=2, ensure_ascii=False)
                    )
                    count = len(recipes) if isinstance(recipes, list) else "?"
                    logger.info(f"kitchenowl: household {hh_id}: {count} recipes")
                except requests.RequestException as e:
                    logger.warning(
                        f"kitchenowl: could not fetch recipes for household {hh_id}: {e}"
                    )

                # Items / ingredients catalogue
                try:
                    items = get(f"/api/household/{hh_id}/item")
                    (hh_dir / "items.json").write_text(
                        json.dumps(items, indent=2, ensure_ascii=False)
                    )
                except requests.RequestException as e:
                    logger.warning(f"kitchenowl: could not fetch items for household {hh_id}: {e}")

                # Shopping lists
                try:
                    shopping = get(f"/api/household/{hh_id}/shoppinglist")
                    (hh_dir / "shoppinglists.json").write_text(
                        json.dumps(shopping, indent=2, ensure_ascii=False)
                    )
                except requests.RequestException as e:
                    logger.warning(
                        f"kitchenowl: could not fetch shoppinglists for household {hh_id}: {e}"
                    )

            archive = backup_dir / f"kitchenowl_{timestamp}.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                tar.add(work, arcname=f"kitchenowl_{timestamp}")

        archive.chmod(0o600)
        self.cleanup_old_files(backup_dir, "kitchenowl_*.tar.gz", context.retention_days)

        return BackupResult(
            service_name=self.service_name,
            worker_type=self.worker_type,
            success=True,
            message=f"API export: {archive.name} ({archive.stat().st_size} bytes)",
            output_files=[archive],
            started_at=started_at,
            finished_at=datetime.now(),
        )
