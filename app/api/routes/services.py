"""Routes for listing services, triggering backups, and toggling enablement."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.api.icons import icon_url
from app.core.env_manager import EnvManager
from app.core.registry import WorkerRegistry
from app.core.scheduler import BackupScheduler


class EnabledUpdate(BaseModel):
    """Request body toggling whether a service is enabled."""

    enabled: bool


class NewService(BaseModel):
    """Request body adding an app (or another instance of one) to the dashboard."""

    type: str
    label: str = ""


class ServiceLabel(BaseModel):
    """Request body renaming a service."""

    label: str


class ServiceEnvUpdate(BaseModel):
    """Request body with one service's settings (plain names, e.g. ADGUARD_URL)."""

    updates: dict[str, str]


def create_router(
    scheduler: BackupScheduler,
    registry: WorkerRegistry,
    env_manager: EnvManager,
) -> APIRouter:
    """Return a router exposing service listing, trigger, and toggle endpoints."""
    router = APIRouter()

    @router.get("/services")
    async def list_services() -> list[dict]:
        """Return all configured services with state and env var metadata."""
        config = scheduler.get_config()
        env_values = env_manager.read()
        result = []
        for svc in config.get("services", []):
            worker_class = registry.get_class(svc["type"])
            suffix = svc.get("env_suffix", "")
            env_var_info = (
                [
                    spec.describe(env_values.get(spec.key + suffix, ""))
                    for spec in worker_class.env_var_specs
                ]
                if worker_class
                else []
            )
            app_name = worker_class.display_name if worker_class else svc["type"]
            result.append(
                {
                    "name": svc["name"],
                    "type": svc["type"],
                    "enabled": svc.get("enabled", False),
                    "display_name": svc.get("label") or app_name,
                    "app_name": app_name,
                    "description": worker_class.description if worker_class else "",
                    "icon": icon_url(svc["type"]),
                    "is_running": scheduler.is_running(svc["name"]),
                    "last_result": scheduler.get_state(svc["name"]),
                    "env_vars": env_var_info,
                }
            )
        return result

    @router.get("/catalog")
    async def catalog() -> list[dict]:
        """Every app this build can back up, and whether it is on the dashboard."""
        types = [s["type"] for s in scheduler.get_config().get("services", [])]
        return [
            {
                "type": worker_type,
                "display_name": worker_class.display_name,
                "description": worker_class.description,
                "icon": icon_url(worker_type),
                "settings": [spec.label for spec in worker_class.env_var_specs],
                "added": types.count(worker_type),
            }
            for worker_type, worker_class in registry.all().items()
        ]

    @router.post("/services")
    async def add_service(body: NewService) -> dict:
        """Put an app on the dashboard (enabled, settings still to fill in)."""
        try:
            svc = scheduler.add_service(body.type, body.label)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=f"Unknown app '{body.type}'") from e
        return {"status": "added", "service": svc["name"]}

    def find(name: str) -> dict:
        svc = next(
            (s for s in scheduler.get_config().get("services", []) if s["name"] == name), None
        )
        if svc is None:
            raise HTTPException(status_code=404, detail=f"Service '{name}' not found")
        return svc

    @router.put("/services/{name}/env-vars")
    async def update_service_env_vars(name: str, body: ServiceEnvUpdate) -> dict:
        """Save one service's settings; extra instances store them under KEY__<n>."""
        svc = find(name)
        worker_class = registry.get_class(svc["type"])
        allowed = {spec.key for spec in worker_class.env_var_specs} if worker_class else set()
        if bad := set(body.updates) - allowed:
            raise HTTPException(status_code=400, detail=f"Unknown settings for '{name}': {bad}")
        suffix = svc.get("env_suffix", "")
        try:
            await asyncio.to_thread(
                env_manager.update, {key + suffix: value for key, value in body.updates.items()}
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"status": "saved", "updated_keys": list(body.updates)}

    @router.put("/services/{name}/label")
    async def rename_service(name: str, body: ServiceLabel) -> dict:
        """Rename a service as shown on the dashboard."""
        find(name)
        scheduler.set_label(name, body.label)
        return {"status": "saved"}

    @router.delete("/services/{name}")
    async def remove_service(name: str) -> dict:
        """Take an app off the dashboard; its .env settings and backups are kept."""
        if scheduler.is_running(name):
            raise HTTPException(status_code=409, detail="Wait for the running backup to finish")
        try:
            scheduler.remove_service(name)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=f"Service '{name}' not found") from e
        return {"status": "removed", "service": name}

    @router.post("/services/{name}/trigger")
    async def trigger_service(name: str, background_tasks: BackgroundTasks) -> dict:
        """Schedule a single named service to run in the background."""
        config = scheduler.get_config()
        svc = next((s for s in config.get("services", []) if s["name"] == name), None)
        if not svc:
            raise HTTPException(status_code=404, detail=f"Service '{name}' not found")
        if scheduler.is_running(name):
            raise HTTPException(status_code=409, detail=f"Service '{name}' is already running")
        background_tasks.add_task(scheduler.run_service, svc)
        return {"status": "triggered", "service": name}

    @router.post("/services/trigger-all")
    async def trigger_all_services(background_tasks: BackgroundTasks) -> dict:
        """Schedule every enabled, idle service to run in the background."""
        config = scheduler.get_config()
        triggered = []
        skipped = []
        for svc in config.get("services", []):
            if not svc.get("enabled", False):
                skipped.append(svc["name"])
                continue
            if scheduler.is_running(svc["name"]):
                skipped.append(svc["name"])
                continue
            background_tasks.add_task(scheduler.run_service, svc)
            triggered.append(svc["name"])
        return {"status": "triggered", "triggered": triggered, "skipped": skipped}

    @router.put("/services/{name}/enabled")
    async def set_service_enabled(name: str, body: EnabledUpdate) -> dict:
        """Enable or disable a service and persist the change to config."""
        config = scheduler.get_config()
        if not any(s["name"] == name for s in config.get("services", [])):
            raise HTTPException(status_code=404, detail=f"Service '{name}' not found")
        try:
            scheduler.set_enabled(name, body.enabled)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=f"Service '{name}' not found") from e
        except OSError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e
        return {"status": "ok", "service": name, "enabled": body.enabled}

    return router
