"""Adding and removing apps from the dashboard (the app picker)."""

import json
from pathlib import Path

from app.api.auth import AuthManager
from app.api.server import create_app
from app.core.env_manager import EnvManager
from app.core.registry import create_default_registry
from app.core.scheduler import BackupScheduler
from tests.test_auth import call


def make(tmp_path: Path):
    config = tmp_path / "services.json"
    config.write_text(json.dumps({"services": [{"name": "n8n", "type": "n8n", "enabled": True}]}))
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
    assert by_type["n8n"]["added"] and not by_type["wikijs"]["added"]
    assert "Wiki.js URL" in by_type["wikijs"]["settings"]

    assert (await call(app, "POST", "/api/services", {"type": "wikijs"}))[0] == 200
    assert (await call(app, "POST", "/api/services", {"type": "wikijs"}))[0] == 409
    assert (await call(app, "POST", "/api/services", {"type": "nope"}))[0] == 404
    saved = json.loads(config.read_text())["services"]
    wikijs = next(s for s in saved if s["name"] == "wikijs")
    # Workers that look settings up through options get them filled in.
    assert wikijs["enabled"] and wikijs["options"]["wikijs_url_env"] == "WIKIJS_URL"

    assert (await call(app, "DELETE", "/api/services/n8n"))[0] == 200
    assert (await call(app, "DELETE", "/api/services/n8n"))[0] == 404
    assert [s["name"] for s in json.loads(config.read_text())["services"]] == ["wikijs"]
    # Settings stay in .env so adding the app back picks them up again.
    assert "N8N_URL=http://n8n:5678" in (tmp_path / ".env").read_text()
