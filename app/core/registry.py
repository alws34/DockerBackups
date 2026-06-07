from __future__ import annotations

from app.core.context import BackupError
from app.workers.base import BackupWorker


class WorkerRegistry:
    def __init__(self) -> None:
        self._registry: dict[str, type[BackupWorker]] = {}

    def register(self, worker_type: str, worker_class: type[BackupWorker]) -> None:
        self._registry[worker_type] = worker_class

    def create(self, service_config: dict) -> BackupWorker:
        worker_type = service_config.get("type", "")
        if worker_type not in self._registry:
            known = ", ".join(self._registry) or "(none registered)"
            raise BackupError(f"Unknown worker type '{worker_type}'. Known types: {known}")
        return self._registry[worker_type](service_config)

    def get_class(self, worker_type: str) -> type[BackupWorker] | None:
        return self._registry.get(worker_type)

    def list_types(self) -> list[str]:
        return list(self._registry)


def create_default_registry() -> WorkerRegistry:
    from app.workers.vaultwarden_encrypted_json import VaultwardenEncryptedJsonWorker
    from app.workers.wikijs import WikiJsWorker
    from app.workers.snipeit import SnipeItWorker
    from app.workers.bar_assistant import BarAssistantWorker
    from app.workers.kitchenowl import KitchenOwlWorker
    from app.workers.linkwarden import LinkwardenWorker

    registry = WorkerRegistry()
    registry.register(VaultwardenEncryptedJsonWorker.worker_type, VaultwardenEncryptedJsonWorker)
    registry.register(WikiJsWorker.worker_type, WikiJsWorker)
    registry.register(SnipeItWorker.worker_type, SnipeItWorker)
    registry.register(BarAssistantWorker.worker_type, BarAssistantWorker)
    registry.register(KitchenOwlWorker.worker_type, KitchenOwlWorker)
    registry.register(LinkwardenWorker.worker_type, LinkwardenWorker)
    return registry
