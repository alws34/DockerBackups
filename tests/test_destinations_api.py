"""The destinations API: settings validation, connection tests, logins, Google OAuth."""

import asyncio
import json
import stat
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlencode, urlparse

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.api.auth import AuthManager
from app.api.routes import destinations as routes
from app.api.server import create_app
from app.core.context import BackupError
from app.core.env_manager import EnvManager
from app.core.registry import create_default_registry
from app.core.scheduler import BackupScheduler
from app.destinations.base import DeviceLogin, UnknownHostKeyError
from app.destinations.google_drive import GoogleDriveDestination
from app.destinations.local_folder import LocalFolderDestination
from tests.test_auth import LAN, call

CLIENT_SECRET = {
    "installed": {
        "client_id": "cid.apps.googleusercontent.com",
        "client_secret": "csecret",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "redirect_uris": ["http://localhost"],
    }
}


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    """Google token/credential files and STATE_ROOT inside tmp_path."""
    monkeypatch.setenv("GOOGLE_TOKENS_FILE", str(tmp_path / "google-tokens.json"))
    monkeypatch.setenv("GOOGLE_CREDENTIALS_FILE", str(tmp_path / "google-credentials.json"))
    monkeypatch.setenv("STATE_ROOT", str(tmp_path / "state"))
    routes._pending_oauth.clear()
    routes._logins.clear()
    return tmp_path


@pytest.fixture
def app(env: Path):
    config = env / "services.json"
    config.write_text(json.dumps({"services": []}))
    env_file = env / ".env"
    env_file.write_text("SFTP_PASSWORD=hunter2\n")
    env_manager = EnvManager(env_file)
    registry = create_default_registry()
    scheduler = BackupScheduler(str(config), registry, env_manager)
    scheduler.load_config()
    auth = AuthManager(env / "state", env_manager, {"AUTH_MODE": "off"})
    return create_app(scheduler, registry, env_manager, auth)


async def get_html(app, path: str, query: dict[str, str]) -> tuple[int, str]:
    """GET with a query string; return status and the body as text."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": urlencode(query).encode(),
        "root_path": "",
        "headers": [(b"host", f"{LAN}:9100".encode())],
        "client": (LAN, 50000),
        "server": (LAN, 9100),
    }
    sent: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        sent.append(message)

    await app(scope, receive, send)
    status = next(m for m in sent if m["type"] == "http.response.start")["status"]
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, body.decode()


# ── listing and settings ─────────────────────────────────────────────────────


async def test_list_masks_secrets(app):
    status, _, listed = await call(app, "GET", "/api/destinations")
    assert status == 200
    by_type = {d["type"]: d for d in listed}
    assert set(by_type) == {"google_drive", "onedrive", "sftp", "smb", "local_folder"}
    password = next(v for v in by_type["sftp"]["env_vars"] if v["key"] == "SFTP_PASSWORD")
    assert password["configured"]
    assert password["value"] == "***"
    assert "hunter2" not in json.dumps(listed)
    assert by_type["google_drive"]["credentials_uploaded"] is False


async def test_update_settings_validates_type_keys_and_values(app, env: Path):
    before = (env / ".env").read_text()
    good = {"updates": {"LOCAL_FOLDER_PATH": "/backups/out", "LOCAL_FOLDER_ENABLED": "true"}}
    assert (await call(app, "PUT", "/api/destinations/nope/env-vars", good))[0] == 404
    # A destination can't be used to write another component's settings.
    foreign = {"updates": {"ADMIN_PASSWORD_HASH": "x"}}
    assert (await call(app, "PUT", "/api/destinations/local_folder/env-vars", foreign))[0] == 400
    smuggle = {"updates": {"LOCAL_FOLDER_PATH": "/ok\nADMIN_PASSWORD_HASH=x"}}
    assert (await call(app, "PUT", "/api/destinations/local_folder/env-vars", smuggle))[0] == 400
    assert (env / ".env").read_text() == before

    status, _, body = await call(app, "PUT", "/api/destinations/local_folder/env-vars", good)
    assert status == 200
    assert sorted(body["updated_keys"]) == sorted(good["updates"])
    saved = (env / ".env").read_text()
    assert 'LOCAL_FOLDER_PATH="/backups/out"' in saved
    assert 'LOCAL_FOLDER_ENABLED="true"' in saved


# ── connection test ──────────────────────────────────────────────────────────


async def test_connection_test_reports_success_and_errors(app, env: Path):
    assert (await call(app, "POST", "/api/destinations/nope/test"))[0] == 404

    _, _, body = await call(app, "POST", "/api/destinations/local_folder/test")
    assert body["ok"] is False
    assert "not set" in body["message"]

    await call(
        app,
        "PUT",
        "/api/destinations/local_folder/env-vars",
        {"updates": {"LOCAL_FOLDER_PATH": str(env / "out")}},
    )
    _, _, body = await call(app, "POST", "/api/destinations/local_folder/test")
    assert body["ok"] is True
    assert body["message"].startswith("Writable")


async def test_connection_test_offers_host_key_and_closes(app):
    dest = MagicMock()
    dest.check.side_effect = UnknownHostKeyError("SHA256:abc")
    with patch.object(LocalFolderDestination, "from_env", return_value=dest):
        _, _, body = await call(app, "POST", "/api/destinations/local_folder/test")
    assert body == {"ok": False, "message": body["message"], "fingerprint": "SHA256:abc"}
    dest.close.assert_called_once()

    dest = MagicMock()
    dest.check.side_effect = RuntimeError("socket exploded")
    with patch.object(LocalFolderDestination, "from_env", return_value=dest):
        _, _, body = await call(app, "POST", "/api/destinations/local_folder/test")
    assert body == {"ok": False, "message": "RuntimeError: socket exploded"}
    dest.close.assert_called_once()


async def test_connection_test_times_out(app):
    dest = MagicMock()
    dest.check.side_effect = lambda: time.sleep(0.5)
    with (
        patch.object(LocalFolderDestination, "from_env", return_value=dest),
        patch.object(routes, "_TEST_TIMEOUT_SECONDS", 0.05),
    ):
        _, _, body = await call(app, "POST", "/api/destinations/local_folder/test")
    assert body["ok"] is False
    assert "No answer" in body["message"]


# ── device-code logins ───────────────────────────────────────────────────────


async def _finish_logins() -> None:
    await asyncio.gather(*routes._login_tasks)


async def test_device_login_start_and_poll(app):
    login = DeviceLogin("WDJB-MJHT", "https://www.google.com/device", 1800, lambda: "me@x.com")
    with patch.object(GoogleDriveDestination, "start_login", return_value=login):
        status, _, body = await call(app, "POST", "/api/destinations/google_drive/login")
    assert status == 200
    assert body["user_code"] == "WDJB-MJHT"
    await _finish_logins()
    _, _, progress = await call(app, "GET", f"/api/destinations/logins/{body['id']}")
    assert progress == {"status": "connected", "message": "me@x.com"}

    assert (await call(app, "GET", "/api/destinations/logins/not-a-login"))[0] == 404


async def test_device_login_failure_and_unsupported(app):
    def denied() -> str:
        raise BackupError("Access was denied on the Google page.")

    login = DeviceLogin("CODE", "https://example.com", 60, denied)
    with patch.object(GoogleDriveDestination, "start_login", return_value=login):
        _, _, body = await call(app, "POST", "/api/destinations/google_drive/login")
    await _finish_logins()
    _, _, progress = await call(app, "GET", f"/api/destinations/logins/{body['id']}")
    assert progress["status"] == "failed"
    assert "denied" in progress["message"]

    status, _, body = await call(app, "POST", "/api/destinations/sftp/login")
    assert status == 400
    assert "does not use a login" in body["detail"]
    assert (await call(app, "DELETE", "/api/destinations/sftp/login"))[0] == 400


async def test_login_sessions_are_capped(app):
    login = DeviceLogin("CODE", "https://example.com", 60, lambda: "a")
    with patch.object(GoogleDriveDestination, "start_login", return_value=login):
        for _ in range(routes._MAX_LOGIN_SESSIONS + 5):
            await call(app, "POST", "/api/destinations/google_drive/login")
    await _finish_logins()
    assert len(routes._logins) == routes._MAX_LOGIN_SESSIONS


async def test_disconnect_google_revokes_and_deletes(app, env: Path):
    tokens = env / "google-tokens.json"
    tokens.write_text(json.dumps({"refresh_token": "rt", "account": "me@x.com"}))
    with patch("app.destinations.google_drive.requests.post") as post:
        status, _, _ = await call(app, "DELETE", "/api/destinations/google_drive/login")
    assert status == 200
    assert not tokens.exists()
    assert post.call_args.kwargs["data"] == {"token": "rt"}


# ── SFTP key upload ──────────────────────────────────────────────────────────


def _openssh_key(passphrase: bytes | None = None) -> str:
    encryption = (
        serialization.BestAvailableEncryption(passphrase)
        if passphrase
        else serialization.NoEncryption()
    )
    return (
        Ed25519PrivateKey.generate()
        .private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, encryption)
        .decode()
    )


async def test_sftp_key_upload(app, env: Path):
    (env / ".env").write_text("")
    status, _, _ = await call(app, "POST", "/api/destinations/sftp/key", {"key": "not a key"})
    assert status == 400
    assert "SFTP_KEY_FILE" not in (env / ".env").read_text()

    pem = _openssh_key()
    status, _, body = await call(app, "POST", "/api/destinations/sftp/key", {"key": pem})
    assert status == 200
    key_path = Path(body["path"])
    assert key_path.parent == env / "state" / "destinations"
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    assert f'SFTP_KEY_FILE="{key_path}"' in (env / ".env").read_text()


@pytest.mark.parametrize(
    ("password", "passphrase", "expected"),
    [
        ("hunter2", b"hunter2", 200),  # encrypted key, password is its passphrase
        ("hunter2", None, 200),  # plain key next to a password used for password login
        ("", b"hunter2", 400),  # encrypted key, no passphrase saved
        ("wrong", b"hunter2", 400),  # wrong passphrase
    ],
)
async def test_sftp_key_upload_passphrase(app, env: Path, password, passphrase, expected):
    (env / ".env").write_text(f"SFTP_PASSWORD={password}\n")
    status, _, _ = await call(
        app, "POST", "/api/destinations/sftp/key", {"key": _openssh_key(passphrase)}
    )
    assert status == expected
    assert (env / "state" / "destinations" / "sftp_id").exists() is (expected == 200)


# ── Google: bring-your-own OAuth client ──────────────────────────────────────


async def _upload(app, content: str) -> tuple[int, dict]:
    status, _, body = await call(
        app, "POST", "/api/destinations/google_drive/credentials", {"json_content": content}
    )
    return status, body


async def test_client_secret_upload_validation(app, env: Path):
    creds = env / "google-credentials.json"
    assert (await _upload(app, "{not json"))[0] == 400
    service_account = {"type": "service_account", "client_email": "x@y.iam.gserviceaccount.com"}
    status, body = await _upload(app, json.dumps(service_account))
    assert status == 400
    assert "service account" in body["detail"]
    status, body = await _upload(app, json.dumps({"web": {"client_id": "x"}}))
    assert status == 400
    assert "client_secret" in body["detail"]
    assert not creds.exists()

    creds.mkdir()  # a Docker bind mount of a missing file creates a directory
    status, body = await _upload(app, json.dumps(CLIENT_SECRET))
    assert status == 200
    assert body["client_id"] == CLIENT_SECRET["installed"]["client_id"]
    assert creds.is_file()
    assert stat.S_IMODE(creds.stat().st_mode) == 0o600
    _, _, listed = await call(app, "GET", "/api/destinations")
    assert next(d for d in listed if d["type"] == "google_drive")["credentials_uploaded"]


async def test_oauth_start_uses_pkce(app):
    path = "/api/destinations/google_drive/oauth/start"
    base = {"redirect_base": f"http://{LAN}:9100/"}
    status, _, body = await call(app, "POST", path, base)
    assert status == 400
    assert "client_secret.json" in body["detail"]

    await _upload(app, json.dumps(CLIENT_SECRET))
    assert (await call(app, "POST", path, {}))[0] == 400
    status, _, body = await call(app, "POST", path, base)
    assert status == 200
    query = parse_qs(urlparse(body["auth_url"]).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["access_type"] == ["offline"]
    assert query["scope"] == ["https://www.googleapis.com/auth/drive.file"]
    callback = f"http://{LAN}:9100/api/destinations/google_drive/oauth/callback"
    assert query["redirect_uri"] == [callback]
    pending = routes._pending_oauth[query["state"][0]]
    assert pending["redirect_uri"] == callback
    # The verifier never leaves the server; only its challenge goes to Google.
    assert pending["code_verifier"] not in body["auth_url"]


CALLBACK = "/api/destinations/google_drive/oauth/callback"


async def test_oauth_callback_rejects_unknown_state(app, env: Path):
    status, page = await get_html(app, CALLBACK, {"code": "c", "state": "forged"})
    assert status == 400
    assert "unknown or expired" in page
    assert not (env / "google-tokens.json").exists()


async def test_oauth_callback_escapes_errors(app):
    routes._pending_oauth["s1"] = {"redirect_uri": "http://x/cb", "code_verifier": "v"}
    flow = MagicMock()
    flow.fetch_token.side_effect = ValueError("<script>alert(1)</script>")
    with patch("google_auth_oauthlib.flow.Flow.from_client_secrets_file", return_value=flow):
        status, page = await get_html(app, CALLBACK, {"code": "c", "state": "s1"})
    assert status == 500
    assert "<script>" not in page
    assert "&lt;script&gt;" in page


async def test_oauth_callback_stores_tokens_once(app, env: Path):
    routes._pending_oauth["s2"] = {"redirect_uri": "http://x/cb", "code_verifier": "ver"}
    flow = MagicMock()
    flow.credentials.refresh_token = "rt"
    flow.credentials.token_uri = "https://oauth2.googleapis.com/token"
    flow.credentials.client_id = "cid"
    flow.credentials.client_secret = "cs"
    with (
        patch("google_auth_oauthlib.flow.Flow.from_client_secrets_file", return_value=flow),
        patch("app.destinations.google_drive._account_email", return_value="me@x.com"),
    ):
        status, page = await get_html(app, CALLBACK, {"code": "abc", "state": "s2"})
        assert status == 200
        assert "connected" in page
        flow.fetch_token.assert_called_once_with(code="abc", code_verifier="ver")
        # The state is single-use: replaying the callback is refused.
        assert (await get_html(app, CALLBACK, {"code": "abc", "state": "s2"}))[0] == 400
    tokens = env / "google-tokens.json"
    assert json.loads(tokens.read_text())["refresh_token"] == "rt"
    assert stat.S_IMODE(tokens.stat().st_mode) == 0o600
