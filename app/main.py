"""Application entrypoint that wires together the scheduler and web API."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

import uvicorn

from app.api.server import create_app
from app.core.env_manager import EnvManager
from app.core.registry import create_default_registry
from app.core.scheduler import BackupScheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger(__name__)


async def main() -> None:
    """Build the scheduler and web server, then run both until interrupted."""
    config_file = os.environ.get("CONFIG_FILE", "/config/services.json")
    env_file_path = os.environ.get("ENV_FILE", "")

    # Prefer an explicit ENV_FILE when it exists, otherwise fall back to ./.env.
    if env_file_path and (configured := Path(env_file_path)).exists():
        env_file = configured
    else:
        env_file = Path(".env")

    if not Path(config_file).exists():
        logger.error(f"Config file not found: {config_file}")
        sys.exit(1)

    registry = create_default_registry()
    env_manager = EnvManager(env_file)
    scheduler = BackupScheduler(config_file, registry, env_manager)
    scheduler.load_config()
    app = create_app(scheduler, registry, env_manager)

    port = int(os.environ.get("WEB_PORT", "8080"))
    uvicorn_config = uvicorn.Config(
        app,
        host="0.0.0.0",  # noqa: S104 — container network; GUI must be reachable from host
        port=port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(uvicorn_config)

    logger.info(f"Starting Homelab Takeout | config={config_file} | web=http://0.0.0.0:{port}")

    await asyncio.gather(
        scheduler.run_forever(),
        server.serve(),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down")
