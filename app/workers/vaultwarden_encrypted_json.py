"""Worker producing an encrypted JSON vault export via the Bitwarden CLI."""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import ClassVar

from app.core.context import BackupContext, BackupError, BackupResult
from app.workers.base import BackupWorker, EnvVarSpec

logger = logging.getLogger(__name__)


class VaultwardenEncryptedJsonWorker(BackupWorker):
    """Produce an encrypted JSON vault export using the ``bw`` Bitwarden CLI."""

    worker_type: ClassVar[str] = "vaultwarden_encrypted_json"
    display_name: ClassVar[str] = "Vaultwarden"
    description: ClassVar[str] = "Daily encrypted JSON vault export using the Bitwarden CLI."
    env_var_specs: ClassVar[list[EnvVarSpec]] = [
        EnvVarSpec(
            key="VAULTWARDEN_URL",
            option_key="vaultwarden_url_env",
            label="Vaultwarden URL",
            description="Base URL of your Vaultwarden instance (e.g. https://vault.example.com)",
            secret=False,
            required=True,
        ),
        EnvVarSpec(
            key="BW_CLIENTID",
            option_key="client_id_env",
            label="API Client ID",
            description="Found in Vaultwarden web UI → Account Settings → Security → API Key",
            secret=False,
            required=True,
        ),
        EnvVarSpec(
            key="BW_CLIENTSECRET",
            option_key="client_secret_env",
            label="API Client Secret",
            description="Secret from the same API Key panel",
            secret=True,
            required=True,
        ),
        EnvVarSpec(
            key="BW_PASSWORD",
            option_key="master_password_env",
            label="Master Password",
            description="Your Vaultwarden master password (needed to unlock vault for export)",
            secret=True,
            required=True,
        ),
        EnvVarSpec(
            key="VAULTWARDEN_EXPORT_PASSWORD",
            option_key="export_password_env",
            label="Export Encryption Password",
            description=(
                "Password used to encrypt the exported JSON backup. "
                "Must differ from master password."
            ),
            secret=True,
            required=True,
        ),
    ]

    def run(self, context: BackupContext) -> BackupResult:
        """Log in via API key, unlock, sync, and write an encrypted JSON export.

        The vault is always locked and logged out in a ``finally`` block so no
        unlocked session is left behind, even on failure.
        """
        started_at = datetime.now()
        bw = self.require_binary("bw")

        vaultwarden_url = self.require_env_by_option(context, "vaultwarden_url_env")
        client_id = self.require_env_by_option(context, "client_id_env")
        client_secret = self.require_env_by_option(context, "client_secret_env")
        master_password = self.require_env_by_option(context, "master_password_env")
        export_password = self.require_env_by_option(context, "export_password_env")

        backup_dir = self.service_backup_dir(context)
        backup_dir.mkdir(parents=True, exist_ok=True)

        state_dir = self.service_state_dir(context)
        state_dir.mkdir(parents=True, exist_ok=True)
        appdata_dir = state_dir / "bw_appdata"
        appdata_dir.mkdir(parents=True, exist_ok=True)

        timestamp = started_at.strftime("%Y%m%d_%H%M%S")
        output_file = backup_dir / f"vaultwarden_encrypted_json_{timestamp}.json"

        base_env = {
            **os.environ,
            "BITWARDENCLI_APPDATA_DIR": str(appdata_dir),
            "BW_CLIENTID": client_id,
            "BW_CLIENTSECRET": client_secret,
            "BW_PASSWORD": master_password,
        }

        session_token: str | None = None
        try:
            self.run_command([bw, "config", "server", vaultwarden_url], env=base_env)

            self.run_command([bw, "logout"], env=base_env, check=False)

            self.run_command(
                [bw, "login", "--apikey"],
                env=base_env,
                redacted_command=[bw, "login", "--apikey", "[credentials redacted]"],
            )

            unlock_result = self.run_command(
                [bw, "unlock", "--passwordenv", "BW_PASSWORD", "--raw"],
                env=base_env,
                redacted_command=[bw, "unlock", "--passwordenv", "BW_PASSWORD", "--raw"],
            )
            session_token = unlock_result.stdout.strip()
            if not session_token:
                raise BackupError("bw unlock returned empty session token")

            session_env = {**base_env, "BW_SESSION": session_token}

            self.run_command([bw, "sync"], env=session_env)

            self.run_command(
                [
                    bw,
                    "export",
                    "--format",
                    "encrypted_json",
                    "--password",
                    export_password,
                    "--output",
                    str(output_file),
                ],
                env=session_env,
                redacted_command=[
                    bw,
                    "export",
                    "--format",
                    "encrypted_json",
                    "--password",
                    "[redacted]",
                    "--output",
                    str(output_file),
                ],
            )

            if not output_file.exists() or output_file.stat().st_size == 0:
                raise BackupError(f"Export file missing or empty: {output_file}")

            output_file.chmod(0o600)
            logger.info(f"Backup written: {output_file} ({output_file.stat().st_size} bytes)")

            self.cleanup_old_files(
                backup_dir,
                "vaultwarden_encrypted_json_*.json",
                context.retention_days,
            )

            return BackupResult(
                service_name=self.service_name,
                worker_type=self.worker_type,
                success=True,
                message=f"Encrypted JSON export: {output_file.name}",
                output_files=[output_file],
                started_at=started_at,
                finished_at=datetime.now(),
            )

        except BackupError:
            raise
        except Exception as e:
            raise BackupError(str(e)) from e
        finally:
            lock_env = {**base_env}
            if session_token:
                lock_env["BW_SESSION"] = session_token
            self.run_command([bw, "lock"], env=lock_env, check=False)
            self.run_command([bw, "logout"], env=lock_env, check=False)
