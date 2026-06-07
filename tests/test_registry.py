"""Tests for the WorkerRegistry registration and lookup behaviour."""

from __future__ import annotations

import pytest

from app.core.context import BackupContext, BackupError, BackupResult
from app.core.registry import WorkerRegistry
from app.workers.base import BackupWorker, EnvVarSpec


class DummyWorker(BackupWorker):
    """Minimal worker used only to exercise the registry."""

    worker_type = "dummy"
    display_name = "Dummy"
    description = "Test worker"
    env_var_specs = [
        EnvVarSpec(
            key="DUMMY_KEY",
            label="Key",
            description="",
            secret=False,
            required=True,
            option_key="key_env",
        )
    ]

    def run(self, context: BackupContext) -> BackupResult:
        """Never invoked; the registry tests do not run the worker."""
        raise NotImplementedError


def test_register_and_create() -> None:
    """A registered type should be instantiable via create()."""
    registry = WorkerRegistry()
    registry.register("dummy", DummyWorker)
    worker = registry.create({"name": "svc", "type": "dummy", "options": {}})
    assert isinstance(worker, DummyWorker)
    assert worker.service_name == "svc"


def test_create_unknown_type_raises() -> None:
    """Creating an unregistered type should raise BackupError."""
    registry = WorkerRegistry()
    with pytest.raises(BackupError, match="Unknown worker type 'nope'"):
        registry.create({"name": "svc", "type": "nope", "options": {}})


def test_get_class_returns_class() -> None:
    """get_class should return the class for known types and None otherwise."""
    registry = WorkerRegistry()
    registry.register("dummy", DummyWorker)
    assert registry.get_class("dummy") is DummyWorker
    assert registry.get_class("missing") is None


def test_list_types() -> None:
    """list_types should report every registered type name."""
    registry = WorkerRegistry()
    registry.register("dummy", DummyWorker)
    registry.register("dummy2", DummyWorker)
    assert set(registry.list_types()) == {"dummy", "dummy2"}
