"""OneDrive uploads, listing, retention and login, with MSAL and Graph mocked."""

import stat
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.core.context import BackupError
from app.destinations import onedrive
from app.destinations.onedrive import OneDriveDestination


def _resp(status: int = 200, body: dict | None = None) -> MagicMock:
    r = MagicMock(status_code=status, text="err")
    r.json.return_value = body or {}
    return r


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    return {"MICROSOFT_CLIENT_ID": "cid", "STATE_ROOT": str(tmp_path)}


@pytest.fixture
def msal_app(env, monkeypatch) -> MagicMock:
    """A signed-in MSAL app whose cache changes on every token use (as MSAL's does)."""
    app = MagicMock()
    app.get_accounts.return_value = [{"username": "me@outlook.com"}]
    app.acquire_token_silent.return_value = {"access_token": "AT"}
    cache = MagicMock(has_state_changed=True)
    cache.serialize.return_value = '{"rotated": true}'
    monkeypatch.setattr(onedrive, "_app", lambda e: (app, cache))
    return app


def test_large_upload_is_chunked_and_upload_url_gets_no_token(
    env, msal_app, tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(onedrive, "_CHUNK", 4)
    src = tmp_path / "wikijs_20261001_033000.tar.gz"
    src.write_bytes(b"0123456789")
    session = _resp(body={"uploadUrl": "https://upload.example/session"})
    with (
        patch.object(onedrive.requests, "request", return_value=session) as graph,
        patch.object(onedrive.requests, "put", return_value=_resp(202)) as put,
    ):
        OneDriveDestination(env).put(src, "wikijs")
    method, url = graph.call_args.args
    assert method == "POST" and url.endswith(
        ":/wikijs/wikijs_20261001_033000.tar.gz:/createUploadSession"
    )
    assert graph.call_args.kwargs["headers"]["Authorization"] == "Bearer AT"
    ranges = [c.kwargs["headers"] for c in put.call_args_list]
    assert ranges == [
        {"Content-Range": "bytes 0-3/10"},
        {"Content-Range": "bytes 4-7/10"},
        {"Content-Range": "bytes 8-9/10"},
    ]
    assert b"".join(c.kwargs["data"] for c in put.call_args_list) == b"0123456789"
    # MSAL rotated the refresh token: the new cache is saved privately.
    cache_file = tmp_path / "destinations" / "onedrive-token-cache.json"
    assert cache_file.read_text() == '{"rotated": true}'
    assert stat.S_IMODE(cache_file.stat().st_mode) == 0o600


def test_failed_chunk_and_empty_file(env, msal_app, tmp_path: Path):
    src = tmp_path / "n8n_20261001_000000.json"
    src.write_bytes(b"{}")
    session = _resp(body={"uploadUrl": "https://upload.example/session"})
    with (
        patch.object(onedrive.requests, "request", return_value=session),
        patch.object(onedrive.requests, "put", return_value=_resp(507)),
    ):
        with pytest.raises(BackupError, match="upload failed 507"):
            OneDriveDestination(env).put(src, "n8n")

    src.write_bytes(b"")
    with patch.object(onedrive.requests, "request", return_value=_resp(201)) as graph:
        OneDriveDestination(env).put(src, "n8n")
    method, url = graph.call_args.args
    assert method == "PUT" and url.endswith(":/n8n/n8n_20261001_000000.json:/content")


def test_ship_pages_listing_and_prunes_only_own_files(env, msal_app, tmp_path: Path):
    src = tmp_path / "n8n_20261003_000000.json"
    src.write_text("{}")
    session = _resp(body={"uploadUrl": "https://upload.example/s"})
    pages = [
        _resp(
            body={
                "value": [{"name": "n8n_20261001_000000.json"}, {"name": "readme.txt"}],
                "@odata.nextLink": "https://graph/next",
            }
        ),
        _resp(body={"value": [{"name": "n8n_20261002_000000.json"}, {"name": src.name}]}),
    ]
    with (
        patch.object(
            onedrive.requests, "request", side_effect=[session, *pages, _resp(204)]
        ) as graph,
        patch.object(onedrive.requests, "put", return_value=_resp(201)),
    ):
        pruned = OneDriveDestination(env).ship(src, "n8n", keep_count=2)
    assert pruned == ["n8n_20261001_000000.json"]
    assert graph.call_args_list[2].args[1] == "https://graph/next"
    method, url = graph.call_args_list[-1].args
    assert method == "DELETE" and url.endswith(":/n8n/n8n_20261001_000000.json:")


def test_missing_folder_lists_empty_and_names_are_quoted(env, msal_app):
    dest = OneDriveDestination(env)
    with patch.object(onedrive.requests, "request", return_value=_resp(404)):
        assert dest.list_names("n8n") == []
    assert dest._item("a/b", "c#d").endswith(":/a%2Fb/c%23d:")


def test_graph_errors_become_backup_errors(env, msal_app):
    dest = OneDriveDestination(env)
    with patch.object(onedrive.requests, "request", return_value=_resp(500)):
        with pytest.raises(BackupError, match="OneDrive error 500"):
            dest.check()
    with patch.object(
        onedrive.requests, "request", side_effect=onedrive.requests.ConnectionError("down")
    ):
        with pytest.raises(BackupError, match="request failed"):
            dest.remove("n8n", "x")


def test_expired_login(env, msal_app):
    msal_app.acquire_token_silent.return_value = {"error": "invalid_grant"}
    with pytest.raises(BackupError, match="expired or was revoked"):
        OneDriveDestination(env)._token()
    msal_app.get_accounts.return_value = []
    with pytest.raises(BackupError, match="expired or was revoked"):
        OneDriveDestination(env)._token()


def test_login_status_and_from_env(env, msal_app, tmp_path: Path):
    with pytest.raises(BackupError, match="not connected"):
        OneDriveDestination.from_env(env)  # no token cache yet
    cache_file = tmp_path / "destinations" / "onedrive-token-cache.json"
    cache_file.parent.mkdir()
    cache_file.write_text("{}")
    assert OneDriveDestination.login_status(env) == {
        "connected": True,
        "account": "me@outlook.com",
    }
    with patch.object(onedrive.requests, "request", return_value=_resp(200)):
        assert "me@outlook.com" in OneDriveDestination.from_env(env).check()
    OneDriveDestination.disconnect(env)
    assert not cache_file.exists()


def test_device_login(env, msal_app, tmp_path: Path):
    msal_app.initiate_device_flow.return_value = {"error_description": "AADSTS700016"}
    with pytest.raises(BackupError, match="AADSTS700016"):
        OneDriveDestination.start_login(env)

    msal_app.initiate_device_flow.return_value = {
        "user_code": "ABCD",
        "verification_uri": "https://microsoft.com/devicelogin",
    }
    msal_app.acquire_token_by_device_flow.return_value = {
        "access_token": "AT",
        "id_token_claims": {"preferred_username": "me@outlook.com"},
    }
    login = OneDriveDestination.start_login(env)
    assert (login.user_code, login.expires_in) == ("ABCD", 900)
    assert login.wait() == "me@outlook.com"
    assert (tmp_path / "destinations" / "onedrive-token-cache.json").is_file()

    msal_app.acquire_token_by_device_flow.return_value = {"error_description": "declined"}
    with pytest.raises(BackupError, match="declined"):
        OneDriveDestination.start_login(env).wait()


def test_no_client_id_means_no_login(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(onedrive.oauth_apps, "MICROSOFT_CLIENT_ID", "")
    env = {"STATE_ROOT": str(tmp_path)}
    assert not OneDriveDestination.login_available(env)
    assert OneDriveDestination.login_status(env) == {"connected": False, "account": ""}
    with pytest.raises(BackupError, match="no built-in Microsoft app"):
        OneDriveDestination.start_login(env)


def test_app_loads_saved_token_cache(env, tmp_path: Path):
    cache_file = tmp_path / "destinations" / "onedrive-token-cache.json"
    cache_file.parent.mkdir()
    cache_file.write_text("{}")
    with (
        patch.object(onedrive.msal, "PublicClientApplication") as pca,
        patch.object(onedrive.msal, "SerializableTokenCache") as cache_cls,
    ):
        onedrive._app(env)
    cache_cls.return_value.deserialize.assert_called_once_with("{}")
    assert pca.call_args.args == ("cid",)
    assert pca.call_args.kwargs["token_cache"] is cache_cls.return_value
