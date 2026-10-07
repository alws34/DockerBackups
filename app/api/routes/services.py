"""Routes for listing services, triggering backups, and toggling enablement."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.api.icons import icon_url
from app.api.routes import run_or_400
from app.core.env_manager import EnvManager
from app.core.registry import WorkerRegistry
from app.core.scheduler import BackupScheduler
from app.workers.base import BackupWorker

# Sonar (S8415) only reads literal status-code keys in `responses=`, not `**` merges.
_NOT_FOUND = {"description": "Service not found"}
_BUSY = {"description": "A backup of the service is running"}


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


def _describe(
    svc: dict,
    worker_class: type[BackupWorker] | None,
    env_values: dict[str, str],
    scheduler: BackupScheduler,
) -> dict:
    """One dashboard card: the service, its app, state and settings (secrets masked)."""
    if worker_class is None:
        app_name, description, env_vars = svc["type"], "", []
    else:
        suffix = svc.get("env_suffix", "")
        app_name, description = worker_class.display_name, worker_class.description
        env_vars = [
            spec.describe(env_values.get(spec.key + suffix, ""))
            for spec in worker_class.env_var_specs
        ]
    return {
        "name": svc["name"],
        "type": svc["type"],
        "enabled": svc.get("enabled", False),
        "display_name": svc.get("label") or app_name,
        "app_name": app_name,
        "description": description,
        "icon": icon_url(svc["type"]),
        "is_running": scheduler.is_running(svc["name"]),
        "last_result": scheduler.get_state(svc["name"]),
        "env_vars": env_vars,
    }


def _find(scheduler: BackupScheduler, name: str) -> dict:
    svc = next((s for s in scheduler.get_config().get("services", []) if s["name"] == name), None)
    if svc is None:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")
    return svc


def _add(scheduler: BackupScheduler, body: NewService) -> dict:
    try:
        return scheduler.add_service(body.type, body.label)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=f"Unknown app '{body.type}'") from e


def _check_keys(worker_class: type[BackupWorker] | None, name: str, updates: dict) -> None:
    allowed = {spec.key for spec in worker_class.env_var_specs} if worker_class else set()
    if bad := set(updates) - allowed:
        raise HTTPException(status_code=400, detail=f"Unknown settings for '{name}': {bad}")


def _remove(scheduler: BackupScheduler, name: str) -> None:
    if scheduler.is_running(name):
        raise HTTPException(status_code=409, detail="Wait for the running backup to finish")
    try:
        scheduler.remove_service(name)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found") from e


def _idle(scheduler: BackupScheduler, name: str) -> dict:
    """Return the named service, or raise if it is unknown or already running."""
    svc = _find(scheduler, name)
    if scheduler.is_running(name):
        raise HTTPException(status_code=409, detail=f"Service '{name}' is already running")
    return svc


def _runnable(scheduler: BackupScheduler) -> tuple[list[dict], list[str]]:
    """Split services into those to run now (enabled, idle) and the names skipped."""
    run: list[dict] = []
    skipped: list[str] = []
    for svc in scheduler.get_config().get("services", []):
        if svc.get("enabled", False) and not scheduler.is_running(svc["name"]):
            run.append(svc)
        else:
            skipped.append(svc["name"])
    return run, skipped


def _set_enabled(scheduler: BackupScheduler, name: str, enabled: bool) -> None:
    _find(scheduler, name)
    try:
        scheduler.set_enabled(name, enabled)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found") from e
    except OSError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


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
        env_values = env_manager.read()
        return [
            _describe(svc, registry.get_class(svc["type"]), env_values, scheduler)
            for svc in scheduler.get_config().get("services", [])
        ]

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

    @router.post("/services", responses={404: {"description": "Unknown app"}})
    async def add_service(body: NewService) -> dict:
        """Put an app on the dashboard (enabled, settings still to fill in)."""
        return {"status": "added", "service": _add(scheduler, body)["name"]}

    @router.put(
        "/services/{name}/env-vars",
        responses={404: _NOT_FOUND, 400: {"description": "Invalid settings"}},
    )
    async def update_service_env_vars(name: str, body: ServiceEnvUpdate) -> dict:
        """Save one service's settings; extra instances store them under KEY__<n>."""
        svc = _find(scheduler, name)
        _check_keys(registry.get_class(svc["type"]), name, body.updates)
        suffix = svc.get("env_suffix", "")
        updates = {key + suffix: value for key, value in body.updates.items()}
        await run_or_400(env_manager.update, updates, errors=(ValueError,))
        return {"status": "saved", "updated_keys": list(body.updates)}

    @router.put("/services/{name}/label", responses={404: _NOT_FOUND})
    async def rename_service(name: str, body: ServiceLabel) -> dict:
        """Rename a service as shown on the dashboard."""
        _find(scheduler, name)
        scheduler.set_label(name, body.label)
        return {"status": "saved"}

    @router.delete("/services/{name}", responses={404: _NOT_FOUND, 409: _BUSY})
    async def remove_service(name: str) -> dict:
        """Take an app off the dashboard; its .env settings and backups are kept."""
        _remove(scheduler, name)
        return {"status": "removed", "service": name}

    @router.post("/services/{name}/trigger", responses={404: _NOT_FOUND, 409: _BUSY})
    async def trigger_service(name: str, background_tasks: BackgroundTasks) -> dict:
        """Schedule a single named service to run in the background."""
        background_tasks.add_task(scheduler.run_service, _idle(scheduler, name))
        return {"status": "triggered", "service": name}

    @router.post("/services/trigger-all")
    async def trigger_all_services(background_tasks: BackgroundTasks) -> dict:
        """Schedule every enabled, idle service to run in the background."""
        run, skipped = _runnable(scheduler)
        for svc in run:
            background_tasks.add_task(scheduler.run_service, svc)
        return {
            "status": "triggered",
            "triggered": [svc["name"] for svc in run],
            "skipped": skipped,
        }

    @router.put(
        "/services/{name}/enabled",
        responses={404: _NOT_FOUND, 500: {"description": "Could not save the config"}},
    )
    async def set_service_enabled(name: str, body: EnabledUpdate) -> dict:
        """Enable or disable a service and persist the change to config."""
        _set_enabled(scheduler, name, body.enabled)
        return {"status": "ok", "service": name, "enabled": body.enabled}

    return router
