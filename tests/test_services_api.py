"""Adding and removing apps from the dashboard (the app picker)."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.api.auth import AuthManager
from app.api.server import create_app
from app.core.env_manager import EnvManager
from app.core.registry import create_default_registry
from app.core.scheduler import BackupScheduler
from tests.test_auth import call


def make(tmp_path: Path, *extra: dict):
    config = tmp_path / "services.json"
    services = [{"name": "n8n", "type": "n8n", "enabled": True}, *extra]
    config.write_text(json.dumps({"services": services}))
    env_file = tmp_path / ".env"
    env_file.write_text("N8N_URL=http://n8n:5678\n")
    env_manager = EnvManager(env_file)
    registry = create_default_registry()
    scheduler = BackupScheduler(str(config), registry, env_manager)
    scheduler.load_config()
    auth = AuthManager(tmp_path / "state", env_manager, {"AUTH_MODE": "off"})
    return create_app(scheduler, registry, env_manager, auth), config


async def test_add_and_remove_service(tmp_path: Path):
    app, config = make(tmp_path)

    _, _, catalog = await call(app, "GET", "/api/catalog")
    by_type = {c["type"]: c for c in catalog}
    assert by_type["n8n"]["added"]
    assert not by_type["wikijs"]["added"]
    assert "Wiki.js URL" in by_type["wikijs"]["settings"]

    assert (await call(app, "POST", "/api/services", {"type": "wikijs"}))[0] == 200
    assert (await call(app, "POST", "/api/services", {"type": "nope"}))[0] == 404
    saved = json.loads(config.read_text())["services"]
    wikijs = next(s for s in saved if s["name"] == "wikijs")
    # Workers that look settings up through options get them filled in.
    assert wikijs["enabled"]
    assert wikijs["options"]["wikijs_url_env"] == "WIKIJS_URL"

    assert (await call(app, "DELETE", "/api/services/n8n"))[0] == 200
    assert (await call(app, "DELETE", "/api/services/n8n"))[0] == 404
    assert [s["name"] for s in json.loads(config.read_text())["services"]] == ["wikijs"]
    # Settings stay in .env so adding the app back picks them up again.
    assert "N8N_URL=http://n8n:5678" in (tmp_path / ".env").read_text()


async def test_second_instance_keeps_its_own_settings(tmp_path: Path):
    app, config = make(tmp_path)
    for _ in range(2):
        assert (await call(app, "POST", "/api/services", {"type": "adguardhome"}))[0] == 200
    names = [s["name"] for s in json.loads(config.read_text())["services"]]
    assert names == ["n8n", "adguardhome", "adguardhome_2"]

    office = {"updates": {"ADGUARD_URL": "http://10.0.0.2:3000"}}
    lab = {"updates": {"ADGUARD_URL": "http://10.0.0.85:8081"}}
    assert (await call(app, "PUT", "/api/services/adguardhome/env-vars", office))[0] == 200
    assert (await call(app, "PUT", "/api/services/adguardhome_2/env-vars", lab))[0] == 200
    bad = {"updates": {"N8N_URL": "x"}}
    assert (await call(app, "PUT", "/api/services/adguardhome_2/env-vars", bad))[0] == 400
    env = (tmp_path / ".env").read_text()
    assert 'ADGUARD_URL="http://10.0.0.2:3000"' in env
    assert 'ADGUARD_URL__2="http://10.0.0.85:8081"' in env

    rename = {"label": "AdGuard (lab)"}
    assert (await call(app, "PUT", "/api/services/adguardhome_2/label", rename))[0] == 200
    _, _, listed = await call(app, "GET", "/api/services")
    second = next(s for s in listed if s["name"] == "adguardhome_2")
    assert second["display_name"] == "AdGuard (lab)"
    assert second["app_name"] == "AdGuard Home"
    url = next(ev for ev in second["env_vars"] if ev["key"] == "ADGUARD_URL")
    assert url["value"] == "http://10.0.0.85:8081"
    _, _, catalog = await call(app, "GET", "/api/catalog")
    assert next(c for c in catalog if c["type"] == "adguardhome")["added"] == 2


def test_worker_of_an_instance_sees_its_own_values(tmp_path: Path):
    scheduler = BackupScheduler(str(tmp_path / "s.json"), create_default_registry())
    env = {"ADGUARD_URL": "http://first", "ADGUARD_URL__2": "http://second", "OTHER": "x"}
    second = {"name": "adguardhome_2", "type": "adguardhome", "env_suffix": "__2"}
    seen = scheduler.instance_env(second, env)
    assert seen["ADGUARD_URL"] == "http://second"
    assert seen["OTHER"] == "x"
    assert seen["ADGUARD_USERNAME"] == ""  # never falls back to the first server's login
    first = {"name": "adguardhome", "type": "adguardhome"}
    assert scheduler.instance_env(first, env)["ADGUARD_URL"] == "http://first"


def _saved(config: Path) -> dict[str, dict]:
    return {s["name"]: s for s in json.loads(config.read_text())["services"]}


async def test_trigger_runs_enabled_services_only(tmp_path: Path):
    app, config = make(tmp_path, {"name": "wikijs", "type": "wikijs", "enabled": True})

    status, _, body = await call(app, "PUT", "/api/services/wikijs/enabled", {"enabled": False})
    assert (status, body["enabled"]) == (200, False)
    assert _saved(config)["wikijs"]["enabled"] is False

    with patch.object(BackupScheduler, "run_service", new=AsyncMock()) as run:
        _, _, body = await call(app, "POST", "/api/services/trigger-all")
        assert (body["triggered"], body["skipped"]) == (["n8n"], ["wikijs"])
        # A single trigger runs the service even while it is disabled.
        assert (await call(app, "POST", "/api/services/wikijs/trigger"))[0] == 200
    assert [c.args[0]["name"] for c in run.await_args_list] == ["n8n", "wikijs"]


async def test_running_service_is_left_alone(tmp_path: Path):
    app, config = make(tmp_path)
    with (
        patch.object(BackupScheduler, "is_running", return_value=True),
        patch.object(BackupScheduler, "run_service", new=AsyncMock()) as run,
    ):
        assert (await call(app, "POST", "/api/services/n8n/trigger"))[0] == 409
        assert (await call(app, "DELETE", "/api/services/n8n"))[0] == 409
        _, _, body = await call(app, "POST", "/api/services/trigger-all")
        assert (body["triggered"], body["skipped"]) == ([], ["n8n"])
    run.assert_not_awaited()
    assert "n8n" in _saved(config)


async def test_unknown_service_is_404_everywhere(tmp_path: Path):
    app, _ = make(tmp_path)
    for method, path, body in [
        ("POST", "/api/services/nope/trigger", None),
        ("PUT", "/api/services/nope/enabled", {"enabled": True}),
        ("PUT", "/api/services/nope/label", {"label": "x"}),
        ("PUT", "/api/services/nope/env-vars", {"updates": {}}),
        ("DELETE", "/api/services/nope", None),
    ]:
        assert (await call(app, method, path, body))[0] == 404, path


async def test_config_write_failure_is_reported(tmp_path: Path):
    app, _ = make(tmp_path)
    with patch.object(BackupScheduler, "_save_config", side_effect=OSError("disk full")):
        status, _, body = await call(app, "PUT", "/api/services/n8n/enabled", {"enabled": False})
    assert (status, body["detail"]) == (500, "disk full")


async def test_setting_with_a_line_break_is_rejected(tmp_path: Path):
    app, _ = make(tmp_path)
    injected = {"updates": {"N8N_URL": "http://n8n\nAUTH_MODE=off"}}
    assert (await call(app, "PUT", "/api/services/n8n/env-vars", injected))[0] == 400
    assert "AUTH_MODE" not in (tmp_path / ".env").read_text()


async def test_service_of_a_removed_app_still_lists(tmp_path: Path):
    app, _ = make(tmp_path, {"name": "gone", "type": "retired_app", "enabled": False})
    _, _, listed = await call(app, "GET", "/api/services")
    gone = next(s for s in listed if s["name"] == "gone")
    assert (gone["app_name"], gone["env_vars"]) == ("retired_app", [])
    # It has no settings, so any key is unknown.
    bad = {"updates": {"N8N_URL": "x"}}
    assert (await call(app, "PUT", "/api/services/gone/env-vars", bad))[0] == 400
