"""Request guards for the web GUI: Host allow-list, cross-site write blocking, security headers.

The GUI holds credentials for every backed-up service, so a malicious web page the admin
visits must not be able to drive it. Two attacks matter:

- DNS rebinding: an attacker's hostname re-resolves to this server, making their page
  same-origin with the GUI. Blocked by only accepting Host headers that are IP literals,
  ``localhost``, or names listed in ``ALLOWED_HOSTS``.
- Cross-site request forgery: another origin submits a form/fetch that changes state.
  Blocked by rejecting non-safe methods whose Fetch Metadata / Origin headers say the
  request came from another site.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    # ponytail: no script-src/style-src yet — the GUI still uses inline onclick handlers and
    # style attributes. Tighten to "script-src 'self'" once the UI redesign removes them.
    "Content-Security-Policy": "frame-ancestors 'none'; object-src 'none'; base-uri 'none'",
}


def parse_allowed_hosts(raw: str) -> frozenset[str]:
    """Parse a comma-separated ``ALLOWED_HOSTS`` value into lowercase hostnames."""
    return frozenset(h.strip().lower() for h in raw.split(",") if h.strip())


def host_allowed(host_header: str, allowed: frozenset[str]) -> bool:
    """Return True if the Host header names an IP literal, localhost, or an allowed name."""
    if "*" in allowed:
        return True
    name = urlsplit(f"//{host_header}").hostname or ""
    if not name:
        return False
    if name == "localhost" or name in allowed:
        return True
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return False
    return True


def is_cross_site_write(method: str, headers: dict[str, str]) -> bool:
    """Return True for a state-changing request that a browser marked as cross-site."""
    if method in _SAFE_METHODS:
        return False
    site = headers.get("sec-fetch-site")
    if site is not None:
        return site not in ("same-origin", "none")
    origin = headers.get("origin")
    if origin is None:
        return False  # not from a browser page (curl, scripts) — nothing to forge
    return urlsplit(origin).netloc != headers.get("host", "")


def install_security_guards(app: FastAPI, allowed_hosts: frozenset[str]) -> None:
    """Attach the Host check, cross-site write check and security headers to ``app``."""

    @app.middleware("http")
    async def guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        headers = {k.lower(): v for k, v in request.headers.items()}
        host = headers.get("host", "")
        if not host_allowed(host, allowed_hosts):
            response: Response = PlainTextResponse(
                f"Host '{host}' is not allowed. Add it to ALLOWED_HOSTS in .env and restart.",
                status_code=400,
            )
        elif is_cross_site_write(request.method, headers):
            response = PlainTextResponse("Cross-site request blocked.", status_code=403)
        else:
            response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        return response
