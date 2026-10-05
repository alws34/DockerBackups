"""Routes for reading and updating per-worker environment variables."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.env_manager import EnvManager
from app.core.registry import WorkerRegistry


class EnvVarUpdate(BaseModel):
    """Request body carrying environment variable updates for a worker."""

    updates: dict[str, str]


def create_router(env_manager: EnvManager, registry: WorkerRegistry) -> APIRouter:
    """Return a router exposing env var read and update endpoints per worker."""
    router = APIRouter()

    @router.get("/env-vars/{service_type}")
    async def get_env_vars(service_type: str) -> list[dict[str, Any]]:
        """Return the env var specs and current values for a worker type."""
        worker_class = registry.get_class(service_type)
        if not worker_class:
            raise HTTPException(status_code=404, detail=f"Unknown worker type '{service_type}'")
        env_values = env_manager.read()
        return [
            {
                "key": spec.key,
                "label": spec.label,
                "description": spec.description,
                "secret": spec.secret,
                "required": spec.required,
                "value": env_values.get(spec.key, ""),
            }
            for spec in worker_class.env_var_specs
        ]

    @router.put("/env-vars/{service_type}")
    async def update_env_vars(service_type: str, body: EnvVarUpdate) -> dict:
        """Persist env var updates for a worker after validating the keys."""
        worker_class = registry.get_class(service_type)
        if not worker_class:
            raise HTTPException(status_code=404, detail=f"Unknown worker type '{service_type}'")
        allowed_keys = {spec.key for spec in worker_class.env_var_specs}
        bad_keys = set(body.updates) - allowed_keys
        if bad_keys:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown env var keys for '{service_type}': {bad_keys}",
            )
        env_manager.update(body.updates)
        return {"status": "saved", "updated_keys": list(body.updates)}

    return router
