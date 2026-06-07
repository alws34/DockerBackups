"""Route for tailing the most recent log file of a service."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter


def create_router() -> APIRouter:
    """Return a router exposing the per-service log tail endpoint."""
    router = APIRouter()

    @router.get("/logs/{service_name}")
    async def get_logs(service_name: str, lines: int = 200) -> dict:
        """Return the last ``lines`` lines from the newest log file of a service."""
        log_root = Path(os.environ.get("LOG_ROOT", "/logs"))
        log_dir = log_root / service_name
        if not log_dir.exists():
            return {"service": service_name, "logs": []}
        log_files = sorted(log_dir.glob("*.log"), reverse=True)
        if not log_files:
            return {"service": service_name, "logs": []}
        latest = log_files[0]
        all_lines = latest.read_text(errors="replace").splitlines()
        return {
            "service": service_name,
            "file": latest.name,
            "logs": all_lines[-lines:],
        }

    return router
