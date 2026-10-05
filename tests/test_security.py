"""Tests for the web GUI's Host allow-list, cross-site write blocking and security headers."""

from fastapi import FastAPI

from app.api.security import (
    SECURITY_HEADERS,
    host_allowed,
    install_security_guards,
    is_cross_site_write,
    parse_allowed_hosts,
)

ALLOWED = parse_allowed_hosts(" Takeout.Home.Lan , backup.example.com")


def test_parse_allowed_hosts_normalises():
    assert ALLOWED == {"takeout.home.lan", "backup.example.com"}
    assert parse_allowed_hosts("") == frozenset()


def test_host_allowed_accepts_ip_literals_localhost_and_listed_names():
    for host in (
        "192.168.1.5:9100",
        "10.0.0.2",
        "[::1]:9100",
        "localhost:9100",
        "takeout.home.lan:9100",
        "TAKEOUT.HOME.LAN",
    ):
        assert host_allowed(host, ALLOWED), host


def test_host_allowed_rejects_unlisted_names():
    # DNS-rebinding attacks arrive with the attacker's hostname in the Host header.
    for host in ("evil.example", "127.0.0.1.nip.io", "takeout.home.lan.evil.example", ""):
        assert not host_allowed(host, ALLOWED), host


def test_host_allowed_wildcard_disables_check():
    assert host_allowed("anything.example", parse_allowed_hosts("*"))


def test_safe_methods_are_never_blocked():
    assert not is_cross_site_write("GET", {"sec-fetch-site": "cross-site"})


def test_fetch_metadata_decides_when_present():
    assert is_cross_site_write("POST", {"sec-fetch-site": "cross-site"})
    # same-site = another app on a sibling subdomain; still not this GUI's origin.
    assert is_cross_site_write("PUT", {"sec-fetch-site": "same-site"})
    assert not is_cross_site_write("POST", {"sec-fetch-site": "same-origin"})
    assert not is_cross_site_write("POST", {"sec-fetch-site": "none"})


def test_origin_fallback_for_browsers_without_fetch_metadata():
    host = {"host": "192.168.1.5:9100"}
    assert not is_cross_site_write("POST", host)  # curl / scripts send no Origin
    assert not is_cross_site_write("POST", {**host, "origin": "http://192.168.1.5:9100"})
    assert is_cross_site_write("POST", {**host, "origin": "https://evil.example"})
    assert is_cross_site_write("POST", {**host, "origin": "null"})


async def _request(app: FastAPI, method: str, headers: dict[str, str]) -> tuple[int, dict]:
    """Drive the ASGI app directly (no httpx dependency) and return status + headers."""
    sent: list[dict] = []
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": "/ping",
        "raw_path": b"/ping",
        "query_string": b"",
        "root_path": "",
        "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 9100),
    }

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        sent.append(message)

    await app(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    return start["status"], {k.decode(): v.decode() for k, v in start["headers"]}


def _app() -> FastAPI:
    app = FastAPI()
    install_security_guards(app, ALLOWED)

    @app.api_route("/ping", methods=["GET", "POST"])
    async def ping() -> dict:
        return {"ok": True}

    return app


async def test_middleware_allows_normal_requests_and_sets_headers():
    status, headers = await _request(_app(), "GET", {"host": "192.168.1.5:9100"})
    assert status == 200
    for name, value in SECURITY_HEADERS.items():
        assert headers[name.lower()] == value


async def test_middleware_rejects_rebinding_host():
    status, headers = await _request(_app(), "GET", {"host": "evil.example"})
    assert status == 400
    assert headers["x-frame-options"] == "DENY"


async def test_middleware_rejects_cross_site_post():
    status, _ = await _request(
        _app(), "POST", {"host": "192.168.1.5:9100", "sec-fetch-site": "cross-site"}
    )
    assert status == 403
