"""Worker exporting Open WebUI chats and workspace configuration via its REST API."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import ClassVar

import requests

from app.core.context import BackupContext, BackupError, BackupResult
from app.workers.base import BackupWorker, EnvVarSpec, fetch_json

# Statuses meaning "this key may not read that": the export is skipped with a note.
# Open WebUI answers 401 for admin-only routes and missing permissions, 403 for
# API-key endpoint restrictions, and 404 for routes an older version lacks.
_SKIPPABLE = (401, 403, 404)


class OpenWebUIWorker(BackupWorker):
    """Export the API key user's chats plus workspace and (for admins) instance config."""

    worker_type: ClassVar[str] = "openwebui"
    display_name: ClassVar[str] = "Open WebUI"
    description: ClassVar[str] = (
        "Exports the key owner's chats with messages, folders, notes, memories, model presets, "
        "prompts, tools, skills, knowledge base file lists and, with an admin key, functions, "
        "the instance config and users; uploaded files and other users' chats are not included."
    )
    env_var_specs: ClassVar[list[EnvVarSpec]] = [
        EnvVarSpec(
            key="OPENWEBUI_URL",
            label="Open WebUI URL",
            description="Base URL of your Open WebUI instance (e.g. https://chat.example.com).",
            secret=False,
            required=True,
        ),
        EnvVarSpec(
            key="OPENWEBUI_API_KEY",
            label="API Key",
            description=(
                "Open WebUI Settings → Account → API keys → Create new secret key (sk-…). "
                "An admin must first turn on Admin Panel → Settings → General → Enable API Keys. "
                "Use an admin's key to also export functions, the instance config and users."
            ),
            secret=True,
            required=True,
        ),
    ]

    def run(self, context: BackupContext) -> BackupResult:
        started_at = datetime.now()
        api = f"{self.require_env(context, 'OPENWEBUI_URL').rstrip('/')}/api/v1"
        key = self.require_env(context, "OPENWEBUI_API_KEY")
        skipped: dict[str, str] = {}

        with requests.Session() as s:
            s.headers["Authorization"] = f"Bearer {key}"

            def get(path: str, **params: object) -> dict | list:
                return fetch_json(s, "GET", f"{api}{path}", params=params)

            def paged(path: str) -> list:
                items: list = []
                page = 1
                while True:
                    data = get(path, page=page)
                    if isinstance(data, list):  # older versions return everything at once
                        return data
                    items.extend(data["items"])
                    if not data["items"] or len(items) >= data["total"]:
                        return items
                    page += 1

            def optional(name: str, fetch: Callable[[], object]) -> object:
                try:
                    return fetch()
                except BackupError as e:
                    resp = getattr(e.__cause__, "response", None)
                    if resp is None or resp.status_code not in _SKIPPABLE:
                        raise
                    skipped[name] = f"HTTP {resp.status_code}: {resp.text[:200]}"
                    return None

            me = self._whoami(s, api)
            files: dict[str, object] = {
                "user": me,
                "chats": self._chats(s, api),
                "chat_tags": optional("chat_tags", lambda: get("/chats/all/tags")),
                "folders": optional(
                    "folders", lambda: [get(f"/folders/{f['id']}") for f in get("/folders/")]
                ),
                "notes": optional(
                    "notes", lambda: [get(f"/notes/{n['id']}") for n in get("/notes/")]
                ),
                "memories": optional("memories", lambda: get("/memories/")),
                "user_settings": optional("user_settings", lambda: get("/users/user/settings")),
                "models": optional("models", lambda: get("/models/export")),
                "prompts": optional("prompts", lambda: get("/prompts/")),
                "tools": optional("tools", lambda: get("/tools/export")),
                "skills": optional("skills", lambda: get("/skills/export")),
                "knowledge": optional(
                    "knowledge",
                    lambda: [
                        {
                            **kb,
                            # File metadata only: drop the extracted text in "data".
                            "files": [
                                {k: v for k, v in f.items() if k != "data"}
                                for f in paged(f"/knowledge/{kb['id']}/files")
                            ],
                        }
                        for kb in paged("/knowledge/")
                    ],
                ),
                "groups": optional("groups", lambda: get("/groups/")),
                "functions": optional(
                    "functions", lambda: get("/functions/export", include_valves="true")
                ),
                "config": optional("config", lambda: get("/configs/export")),
                "users": optional("users", lambda: get("/users/all")),
            }

        if me.get("role") != "admin":
            skipped.setdefault("admin_only", "functions, config and users need an admin's key")
        files = {k: v for k, v in files.items() if v is not None}
        files["skipped"] = skipped
        return self.archive_json(context, started_at, files)

    @staticmethod
    def _whoami(s: requests.Session, api: str) -> dict:
        """Validate the key and return the user's profile (role, permissions)."""
        try:
            resp = s.get(f"{api}/auths/", timeout=30)
        except requests.RequestException as e:
            raise BackupError(f"Cannot reach Open WebUI at {api}: {e}") from e
        if resp.status_code == 401:
            raise BackupError("OPENWEBUI_API_KEY was rejected (401): create a new key")
        if resp.status_code == 403:
            raise BackupError(
                "Open WebUI refused API key access (403): an admin must enable "
                "Admin Panel → Settings → General → Enable API Keys, grant non-admin users the "
                "API Keys permission, and allow /api/v1 if endpoint restrictions are on"
            )
        if not resp.ok:
            raise BackupError(f"GET {api}/auths/ failed: HTTP {resp.status_code}")
        user = resp.json()
        user.pop("token", None)  # echoes the API key back
        return user

    @staticmethod
    def _chats(s: requests.Session, api: str) -> list:
        """All the user's chats (incl. archived), in the format Import Chats accepts.

        Current versions stream NDJSON; older ones return a JSON array.
        """
        url = f"{api}/chats/all"
        try:
            resp = s.get(url, timeout=300, headers={"Accept": "application/x-ndjson"})
            resp.raise_for_status()
        except requests.RequestException as e:
            raise BackupError(f"GET {url} failed: {e}") from e
        text = resp.text.strip()
        try:
            if text.startswith("["):
                return json.loads(text)
            return [json.loads(line) for line in text.splitlines() if line.strip()]
        except ValueError as e:
            raise BackupError(f"GET {url} returned unreadable data: {text[:200]!r}") from e
