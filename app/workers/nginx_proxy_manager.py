"""Worker exporting Nginx Proxy Manager hosts, access lists and settings via its REST API."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

import requests

from app.core.context import BackupContext, BackupResult
from app.workers.base import BackupWorker, EnvVarSpec, fetch_json

# (output name, API path)
_ENDPOINTS = [
    ("proxy_hosts", "nginx/proxy-hosts"),
    ("redirection_hosts", "nginx/redirection-hosts"),
    ("dead_hosts", "nginx/dead-hosts"),
    ("streams", "nginx/streams"),
    ("access_lists", "nginx/access-lists?expand=items,clients"),
    ("certificates", "nginx/certificates"),
    ("users", "users"),
    ("settings", "settings"),
]


class NginxProxyManagerWorker(BackupWorker):
    """Export NPM configuration via its REST API (token login with user credentials)."""

    worker_type: ClassVar[str] = "nginx_proxy_manager"
    display_name: ClassVar[str] = "Nginx Proxy Manager"
    description: ClassVar[str] = (
        "Exports proxy/redirection/404 hosts, streams, access lists, certificate "
        "metadata (not private keys), users and settings."
    )
    env_var_specs: ClassVar[list[EnvVarSpec]] = [
        EnvVarSpec(
            key="NPM_URL",
            label="NPM Admin URL",
            description="Admin UI base URL (e.g. http://127.0.0.1:81).",
            secret=False,
            required=True,
        ),
        EnvVarSpec(
            key="NPM_EMAIL",
            label="Admin Email",
            description="Login email of an NPM admin user.",
            secret=False,
            required=True,
        ),
        EnvVarSpec(
            key="NPM_PASSWORD",
            label="Admin Password",
            description="Password for that NPM user.",
            secret=True,
            required=True,
        ),
    ]

    def run(self, context: BackupContext) -> BackupResult:
        started_at = datetime.now()
        api = f"{self.require_env(context, 'NPM_URL').rstrip('/')}/api"
        creds = {
            "identity": self.require_env(context, "NPM_EMAIL"),
            "secret": self.require_env(context, "NPM_PASSWORD"),
        }
        with requests.Session() as s:
            token = fetch_json(s, "POST", f"{api}/tokens", json=creds)["token"]
            s.headers["Authorization"] = f"Bearer {token}"
            files = {name: fetch_json(s, "GET", f"{api}/{path}") for name, path in _ENDPOINTS}
        return self.archive_json(context, started_at, files)
