"""Tests for GUI login: setup code, sessions, lockout, password change, proxy/off modes."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.api import auth as auth_module
from app.api.auth import AuthManager, hash_password, verify_password
from app.api.server import create_app
from app.core.env_manager import EnvManager

LAN = "192.168.1.20"
PASSWORD = "correct horse battery"


async def call(
    app,
    method: str,
    path: str,
    body: dict | None = None,
    cookie: str = "",
    client: str = LAN,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], dict]:
    """Drive the ASGI app directly; return status, headers (lowercase), JSON body."""
    raw = json.dumps(body).encode() if body is not None else b""
    hdrs = {"host": f"{LAN}:9100", **(headers or {})}
    if body is not None:
        hdrs["content-type"] = "application/json"
    if cookie:
        hdrs["cookie"] = f"ht_session={cookie}"
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(k.encode(), v.encode()) for k, v in hdrs.items()],
        "client": (client, 50000),
        "server": (LAN, 9100),
    }
    sent: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.request", "body": raw, "more_body": False}

    async def send(message: dict) -> None:
        sent.append(message)

    await app(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    out_headers: dict[str, str] = {}
    for k, v in start["headers"]:
        out_headers.setdefault(k.decode(), v.decode())
    payload = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    is_json = out_headers.get("content-type", "").startswith("application/json")
    return start["status"], out_headers, json.loads(payload) if is_json and payload else {}


def session_from(headers: dict[str, str]) -> str:
    return headers["set-cookie"].split(";")[0].split("=", 1)[1]


@pytest.fixture
def make_app(tmp_path: Path):
    def build(env: dict[str, str] | None = None):
        env_file = tmp_path / ".env"
        env_file.touch()
        env_manager = EnvManager(env_file)
        scheduler = MagicMock()
        scheduler.get_settings.return_value = {"daily_at": "03:30"}
        auth = AuthManager(tmp_path / "state", env_manager, env or {})
        return create_app(scheduler, MagicMock(), env_manager, auth), auth

    return build


def test_hash_roundtrip():
    stored = hash_password(PASSWORD)
    assert stored.startswith("scrypt$")
    assert verify_password(PASSWORD, stored)
    assert not verify_password("wrong password!", stored)
    assert not verify_password(PASSWORD, "garbage")


async def test_first_run_needs_setup_code_from_logs(make_app, tmp_path: Path):
    app, auth = make_app()
    code = (tmp_path / "state" / "setup-code.txt").read_text().strip()
    assert code == auth.setup_code

    status, _, body = await call(app, "GET", "/api/auth/status")
    assert body["setup_required"]
    assert not body["authenticated"]
    assert (await call(app, "GET", "/api/settings"))[0] == 401

    status, _, _ = await call(
        app, "POST", "/api/auth/setup", {"setup_code": "AAAA-BBBB-CCCC", "password": PASSWORD}
    )
    assert status == 403
    status, _, body = await call(
        app, "POST", "/api/auth/setup", {"setup_code": code, "password": "short"}
    )
    assert status == 400
    assert "12" in body["detail"]

    status, headers, _ = await call(
        app, "POST", "/api/auth/setup", {"setup_code": code.lower(), "password": PASSWORD}
    )
    assert status == 200
    cookie_attrs = headers["set-cookie"].lower()
    assert "httponly" in cookie_attrs
    assert "samesite=lax" in cookie_attrs
    token = session_from(headers)
    assert (await call(app, "GET", "/api/settings", cookie=token))[0] == 200
    assert not (tmp_path / "state" / "setup-code.txt").exists()
    assert (await call(app, "POST", "/api/auth/setup", {"setup_code": code, "password": PASSWORD}))[
        0
    ] == 409


async def test_login_lockout_and_logout(make_app, monkeypatch):
    app, auth = make_app()
    auth.set_password(PASSWORD)
    for _ in range(auth_module.MAX_FAILURES):
        assert (await call(app, "POST", "/api/auth/login", {"password": "nope nope nope"}))[
            0
        ] == 401
    # Locked: even the right password is refused for a while, from this client only.
    assert (await call(app, "POST", "/api/auth/login", {"password": PASSWORD}))[0] == 429
    status, headers, _ = await call(
        app, "POST", "/api/auth/login", {"password": PASSWORD}, client="10.0.0.9"
    )
    assert status == 200
    token = session_from(headers)
    assert (await call(app, "GET", "/api/settings", cookie=token))[0] == 200
    await call(app, "POST", "/api/auth/logout", cookie=token)
    assert (await call(app, "GET", "/api/settings", cookie=token))[0] == 401


async def test_password_change_signs_out_other_sessions(make_app):
    app, auth = make_app()
    auth.set_password(PASSWORD)
    _, h1, _ = await call(app, "POST", "/api/auth/login", {"password": PASSWORD})
    _, h2, _ = await call(app, "POST", "/api/auth/login", {"password": PASSWORD})
    phone, laptop = session_from(h1), session_from(h2)
    body = {"current_password": "wrong", "new_password": "a brand new passphrase"}
    assert (await call(app, "POST", "/api/auth/password", body, cookie=laptop))[0] == 403
    body["current_password"] = PASSWORD
    status, headers, _ = await call(app, "POST", "/api/auth/password", body, cookie=laptop)
    assert status == 200
    assert (await call(app, "GET", "/api/settings", cookie=phone))[0] == 401
    assert (await call(app, "GET", "/api/settings", cookie=session_from(headers)))[0] == 200


async def test_proxy_mode_trusts_header_only_from_trusted_proxy(make_app):
    app, _ = make_app({"AUTH_MODE": "proxy", "AUTH_TRUSTED_PROXIES": "10.0.0.5"})
    as_user = {"remote-user": "alon"}
    assert (await call(app, "GET", "/api/settings", client="10.0.0.5", headers=as_user))[0] == 200
    assert (await call(app, "GET", "/api/settings", client="10.0.0.5"))[0] == 401
    # Anyone else on the LAN sending the same header is not trusted.
    assert (await call(app, "GET", "/api/settings", client=LAN, headers=as_user))[0] == 401
    _, _, body = await call(app, "GET", "/api/auth/status", client="10.0.0.5", headers=as_user)
    assert body["authenticated"]
    assert body["user"] == "alon"


async def test_off_mode_and_settings_need_current_password(make_app):
    app, auth = make_app({"AUTH_MODE": "off"})
    assert (await call(app, "GET", "/api/settings"))[0] == 200
    auth.set_password(PASSWORD)
    bad = {"mode": "password", "current_password": "wrong"}
    assert (await call(app, "PUT", "/api/auth/settings", bad))[0] == 403
    no_proxy = {"mode": "proxy", "trusted_proxies": "", "current_password": PASSWORD}
    assert (await call(app, "PUT", "/api/auth/settings", no_proxy))[0] == 400
    ok = {"mode": "password", "current_password": PASSWORD}
    assert (await call(app, "PUT", "/api/auth/settings", ok))[0] == 200
    assert (await call(app, "GET", "/api/settings"))[0] == 401


async def test_unauthenticated_responses_still_get_security_headers(make_app):
    app, _ = make_app()
    status, headers, _ = await call(app, "GET", "/api/settings")
    assert status == 401
    assert headers["x-frame-options"] == "DENY"
    assert "script-src 'self'" in headers["content-security-policy"]
    assert (await call(app, "GET", "/docs"))[0] == 404


async def test_any_hostname_works_with_sign_in_but_not_without(make_app):
    # Reverse-proxy names need no ALLOWED_HOSTS entry: without a session cookie for that
    # name the API answers 401 anyway, so a DNS-rebinding page gets nothing.
    app, _ = make_app()
    proxied = {"host": "backup.home.example"}
    assert (await call(app, "GET", "/", headers=proxied))[0] == 200
    assert (await call(app, "GET", "/api/settings", headers=proxied))[0] == 401
    # With sign-in off the Host allow-list is the only defence, so it applies.
    app, _ = make_app({"AUTH_MODE": "off"})
    assert (await call(app, "GET", "/api/settings", headers=proxied))[0] == 400
    assert (await call(app, "GET", "/api/settings"))[0] == 200


async def test_page_and_static_stay_public(make_app):
    app, _ = make_app()
    assert (await call(app, "GET", "/"))[0] == 200
    assert (await call(app, "GET", "/static/app.js"))[0] == 200
