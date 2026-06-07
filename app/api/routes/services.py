"""Routes for listing services, triggering backups, and toggling enablement."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.core.env_manager import EnvManager
from app.core.registry import WorkerRegistry
from app.core.scheduler import BackupScheduler


class EnabledUpdate(BaseModel):
    """Request body toggling whether a service is enabled."""

    enabled: bool


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
            env_var_info = (
                [
                    spec.describe(env_values.get(spec.key, ""))
                    for spec in worker_class.env_var_specs
                ]
                if worker_class
                else []
            )
            result.append(
                {
                    "name": svc["name"],
                    "type": svc["type"],
                    "enabled": svc.get("enabled", False),
                    "display_name": (
                        worker_class.display_name if worker_class else svc["type"]
                    ),
                    "description": worker_class.description if worker_class else "",
                    "is_running": scheduler.is_running(svc["name"]),
                    "last_result": scheduler.get_state(svc["name"]),
                    "env_vars": env_var_info,
                }
            )
        return result

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
