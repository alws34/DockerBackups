"""Routes for reading and updating the backup schedule and retention settings."""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.scheduler import BackupScheduler

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class SettingsUpdate(BaseModel):
    """Request body for updating schedule and retention settings."""

    daily_at: str
    interval_hours: int = 0
    run_on_start: bool
    keep_days: int


def create_router(scheduler: BackupScheduler) -> APIRouter:
    """Return a router exposing the settings read and update endpoints."""
    router = APIRouter()

    @router.get("/settings")
    async def get_settings() -> dict:
        """Return the current schedule and retention settings."""
        return scheduler.get_settings()

    @router.put("/settings")
    async def update_settings(body: SettingsUpdate) -> dict:
        """Validate and persist updated schedule and retention settings."""
        if not _TIME_RE.match(body.daily_at):
            raise HTTPException(status_code=400, detail="daily_at must be HH:MM (24-hour)")
        if body.keep_days < 1:
            raise HTTPException(status_code=400, detail="keep_days must be >= 1")
        if body.interval_hours < 0:
            raise HTTPException(status_code=400, detail="interval_hours must be >= 0")
        scheduler.update_settings(
            body.daily_at, body.interval_hours, body.run_on_start, body.keep_days
        )
        return {"status": "saved"}

    return router
