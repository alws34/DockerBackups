"""Worker exporting AdGuard Home configuration via its /control REST API."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

import requests

from app.core.context import BackupContext, BackupResult
from app.workers.base import BackupWorker, EnvVarSpec, fetch_json

# (output name, /control path)
_ENDPOINTS = [
    ("status", "status"),
    ("dns_info", "dns_info"),
    ("filtering", "filtering/status"),
    ("rewrites", "rewrite/list"),
    ("clients", "clients"),
    ("blocked_services", "blocked_services/get"),
    ("access", "access/list"),
    ("dhcp", "dhcp/status"),
    ("tls", "tls/status"),
    ("querylog_config", "querylog/config"),
    ("stats_config", "stats/config"),
    ("safebrowsing", "safebrowsing/status"),
    ("parental", "parental/status"),
    ("safesearch", "safesearch/status"),
]


class AdGuardHomeWorker(BackupWorker):
    """Export AdGuard Home settings, filters, rewrites and clients (basic auth)."""

    worker_type: ClassVar[str] = "adguardhome"
    display_name: ClassVar[str] = "AdGuard Home"
    description: ClassVar[str] = (
        "Exports DNS settings, filter lists + user rules, rewrites, clients, "
        "blocked services, access lists, DHCP and TLS config."
    )
    env_var_specs: ClassVar[list[EnvVarSpec]] = [
        EnvVarSpec(
            key="ADGUARD_URL",
            label="AdGuard Home URL",
            description="Web UI base URL (e.g. http://127.0.0.1:3000).",
            secret=False,
            required=True,
        ),
        EnvVarSpec(
            key="ADGUARD_USERNAME",
            label="Username",
            description="AdGuard Home web UI username.",
            secret=False,
            required=True,
        ),
        EnvVarSpec(
            key="ADGUARD_PASSWORD",
            label="Password",
            description="AdGuard Home web UI password.",
            secret=True,
            required=True,
        ),
    ]

    def run(self, context: BackupContext) -> BackupResult:
        started_at = datetime.now()
        api = f"{self.require_env(context, 'ADGUARD_URL').rstrip('/')}/control"
        with requests.Session() as s:
            s.auth = (
                self.require_env(context, "ADGUARD_USERNAME"),
                self.require_env(context, "ADGUARD_PASSWORD"),
            )
            files = {name: fetch_json(s, "GET", f"{api}/{path}") for name, path in _ENDPOINTS}
        return self.archive_json(context, started_at, files)
