"""Registry mapping worker type names to their worker classes."""

from __future__ import annotations

from app.core.context import BackupError
from app.workers.base import BackupWorker


class WorkerRegistry:
    """Maps worker type names to classes and instantiates them on demand."""

    def __init__(self) -> None:
        self._registry: dict[str, type[BackupWorker]] = {}

    def register(self, worker_type: str, worker_class: type[BackupWorker]) -> None:
        """Associate a worker type name with its class."""
        self._registry[worker_type] = worker_class

    def create(self, service_config: dict) -> BackupWorker:
        """Instantiate the worker for the given service config's ``type``."""
        worker_type = service_config.get("type", "")
        if worker_type not in self._registry:
            known = ", ".join(self._registry) or "(none registered)"
            raise BackupError(f"Unknown worker type '{worker_type}'. Known types: {known}")
        return self._registry[worker_type](service_config)

    def get_class(self, worker_type: str) -> type[BackupWorker] | None:
        """Return the worker class for a type, or ``None`` if not registered."""
        return self._registry.get(worker_type)

    def list_types(self) -> list[str]:
        """Return the names of all registered worker types."""
        return list(self._registry)


def create_default_registry() -> WorkerRegistry:
    """Build a registry pre-populated with all built-in workers.

    Worker imports are deferred to function scope to avoid import cycles
    between the registry and the worker modules.
    """
    from app.workers.bar_assistant import BarAssistantWorker
    from app.workers.kitchenowl import KitchenOwlWorker
    from app.workers.linkwarden import LinkwardenWorker
    from app.workers.snipeit import SnipeItWorker
    from app.workers.vaultwarden_encrypted_json import VaultwardenEncryptedJsonWorker
    from app.workers.wikijs import WikiJsWorker

    registry = WorkerRegistry()
    registry.register(VaultwardenEncryptedJsonWorker.worker_type, VaultwardenEncryptedJsonWorker)
    registry.register(WikiJsWorker.worker_type, WikiJsWorker)
    registry.register(SnipeItWorker.worker_type, SnipeItWorker)
    registry.register(BarAssistantWorker.worker_type, BarAssistantWorker)
    registry.register(KitchenOwlWorker.worker_type, KitchenOwlWorker)
    registry.register(LinkwardenWorker.worker_type, LinkwardenWorker)
    return registry
