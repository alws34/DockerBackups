import pytest
from app.core.context import BackupContext, BackupError, BackupResult
from app.core.registry import WorkerRegistry
from app.workers.base import BackupWorker, EnvVarSpec


class DummyWorker(BackupWorker):
    worker_type = "dummy"
    display_name = "Dummy"
    description = "Test worker"
    env_var_specs = [
        EnvVarSpec(key="DUMMY_KEY", label="Key", description="", secret=False, required=True, option_key="key_env")
    ]

    def run(self, context: BackupContext) -> BackupResult:
        raise NotImplementedError


def test_register_and_create():
    registry = WorkerRegistry()
    registry.register("dummy", DummyWorker)
    worker = registry.create({"name": "svc", "type": "dummy", "options": {}})
    assert isinstance(worker, DummyWorker)
    assert worker.service_name == "svc"


def test_create_unknown_type_raises():
    registry = WorkerRegistry()
    with pytest.raises(BackupError, match="Unknown worker type 'nope'"):
        registry.create({"name": "svc", "type": "nope", "options": {}})


def test_get_class_returns_class():
    registry = WorkerRegistry()
    registry.register("dummy", DummyWorker)
    assert registry.get_class("dummy") is DummyWorker
    assert registry.get_class("missing") is None


def test_list_types():
    registry = WorkerRegistry()
    registry.register("dummy", DummyWorker)
    registry.register("dummy2", DummyWorker)
    assert set(registry.list_types()) == {"dummy", "dummy2"}
