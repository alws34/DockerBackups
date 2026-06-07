from __future__ import annotations

import logging
import shutil
import tarfile
from datetime import datetime
from pathlib import Path

import requests

from app.core.context import BackupContext, BackupError, BackupResult
from app.workers.base import BackupWorker, EnvVarSpec

logger = logging.getLogger(__name__)

_LIST_QUERY = """
query {
  pages {
    list(orderBy: PATH) {
      id
      title
      path
      updatedAt
      isPublished
    }
  }
}
"""

_PAGE_QUERY = """
query ($id: Int!) {
  pages {
    single(id: $id) {
      id
      title
      path
      content
      contentType
      updatedAt
    }
  }
}
"""


class WikiJsWorker(BackupWorker):
    worker_type = "wikijs"
    display_name = "Wiki.js"
    description = "Export all pages via GraphQL API and archive as compressed tar."
    env_var_specs = [
        EnvVarSpec(
            key="WIKIJS_URL",
            option_key="wikijs_url_env",
            label="Wiki.js URL",
            description="Base URL of your Wiki.js instance (e.g. https://wiki.example.com)",
            secret=False,
            required=True,
        ),
        EnvVarSpec(
            key="WIKIJS_API_TOKEN",
            option_key="api_token_env",
            label="API Token",
            description="Admin API token from Wiki.js Administration → API Access",
            secret=True,
            required=True,
        ),
    ]

    def run(self, context: BackupContext) -> BackupResult:
        started_at = datetime.now()
        wikijs_url = self.require_env_by_option(context, "wikijs_url_env").rstrip("/")
        api_token = self.require_env_by_option(context, "api_token_env")

        graphql_url = f"{wikijs_url}/graphql"
        headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        }

        backup_dir = self.service_backup_dir(context)
        backup_dir.mkdir(parents=True, exist_ok=True)

        try:
            pages = self._list_pages(graphql_url, headers)
            logger.info(f"[{self.service_name}] Found {len(pages)} pages to export")

            timestamp = started_at.strftime("%Y%m%d_%H%M%S")
            export_dir = backup_dir / f"wikijs_export_{timestamp}"
            export_dir.mkdir(parents=True, exist_ok=True)

            for page in pages:
                page_data = self._fetch_page(graphql_url, headers, page["id"])
                self._write_page(export_dir, page_data)

            output_file = backup_dir / f"wikijs_{timestamp}.tar.gz"
            with tarfile.open(output_file, "w:gz") as tar:
                tar.add(export_dir, arcname="wikijs_export")
            output_file.chmod(0o600)

            shutil.rmtree(export_dir)

            self.cleanup_old_files(backup_dir, "wikijs_*.tar.gz", context.retention_days)

            return BackupResult(
                service_name=self.service_name,
                worker_type=self.worker_type,
                success=True,
                message=f"Exported {len(pages)} pages",
                output_files=[output_file],
                started_at=started_at,
                finished_at=datetime.now(),
            )
        except BackupError:
            raise
        except Exception as e:
            raise BackupError(str(e)) from e

    def _graphql(self, url: str, headers: dict, query: str, variables: dict | None = None) -> dict:
        payload: dict = {"query": query}
        if variables:
            payload["variables"] = variables
        response = requests.post(url, json=payload, headers=headers, timeout=60)
        response.raise_for_status()
        data = response.json()
        if "errors" in data:
            raise BackupError(f"GraphQL error: {data['errors']}")
        return data

    def _list_pages(self, url: str, headers: dict) -> list[dict]:
        data = self._graphql(url, headers, _LIST_QUERY)
        return data["data"]["pages"]["list"]

    def _fetch_page(self, url: str, headers: dict, page_id: int) -> dict:
        data = self._graphql(url, headers, _PAGE_QUERY, {"id": page_id})
        return data["data"]["pages"]["single"]

    def _write_page(self, export_dir: Path, page: dict) -> None:
        page_path = page.get("path", "unknown")
        content = page.get("content", "")
        content_type = page.get("contentType", "markdown")
        extension = ".html" if content_type == "html" else ".md"
        file_path = export_dir / f"{page_path}{extension}"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
