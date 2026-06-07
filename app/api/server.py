"""FastAPI application factory wiring routers, static assets, and the index page."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import destinations, env_vars, logs, services, settings
from app.core.env_manager import EnvManager
from app.core.registry import WorkerRegistry
from app.core.scheduler import BackupScheduler

STATIC_DIR = Path(__file__).parent / "static"


def create_app(
    scheduler: BackupScheduler,
    registry: WorkerRegistry,
    env_manager: EnvManager,
) -> FastAPI:
    """Create and configure the FastAPI app for the backup agent."""
    app = FastAPI(title="Service Backup Agent", version="1.0.0")

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    app.include_router(
        services.create_router(scheduler, registry, env_manager), prefix="/api"
    )
    app.include_router(logs.create_router(), prefix="/api")
    app.include_router(env_vars.create_router(env_manager, registry), prefix="/api")
    app.include_router(destinations.create_router(env_manager), prefix="/api")
    app.include_router(settings.create_router(scheduler), prefix="/api")

    @app.get("/")
    async def index() -> FileResponse:
        """Serve the single-page web UI."""
        return FileResponse(str(STATIC_DIR / "index.html"))

    return app
