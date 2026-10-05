"""FastAPI application factory wiring routers, static assets, and the index page."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.auth import AuthManager
from app.api.routes import auth as auth_routes
from app.api.routes import destinations, env_vars, logs, services, settings
from app.api.security import install_security_guards, parse_allowed_hosts
from app.core.env_manager import EnvManager
from app.core.registry import WorkerRegistry
from app.core.scheduler import BackupScheduler

STATIC_DIR = Path(__file__).parent / "static"


def create_app(
    scheduler: BackupScheduler,
    registry: WorkerRegistry,
    env_manager: EnvManager,
    auth: AuthManager,
) -> FastAPI:
    """Create and configure the FastAPI app for Homelab Takeout."""
    app = FastAPI(title="Homelab Takeout", version="1.0.0")

    # Starlette runs the last-added middleware first: the Host/cross-site guards must
    # wrap the login check, so they run before it and their headers reach 401s too.
    auth_routes.install_auth_guard(app, auth)
    allowed_hosts = os.environ.get("ALLOWED_HOSTS") or env_manager.read().get("ALLOWED_HOSTS", "")
    install_security_guards(app, parse_allowed_hosts(allowed_hosts))

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    app.include_router(services.create_router(scheduler, registry, env_manager), prefix="/api")
    app.include_router(logs.create_router(), prefix="/api")
    app.include_router(auth_routes.create_router(auth), prefix="/api")
    app.include_router(env_vars.create_router(env_manager, registry), prefix="/api")
    app.include_router(destinations.create_router(env_manager, scheduler), prefix="/api")
    app.include_router(settings.create_router(scheduler), prefix="/api")

    @app.get("/")
    async def index() -> FileResponse:
        """Serve the single-page web UI."""
        return FileResponse(str(STATIC_DIR / "index.html"))

    return app
