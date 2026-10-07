"""Login, first-run setup, logout and auth settings for the web GUI."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.api.auth import SESSION_COOKIE, SESSION_MAX_SECONDS, AuthManager
from app.core.context import BackupError

# Reachable without a session: the login screen needs these.
_PUBLIC_API = {
    "/api/auth/status",
    "/api/auth/login",
    "/api/auth/setup",
    # Google redirects the browser here; the one-time OAuth state authenticates it.
    "/api/destinations/google_drive/oauth/callback",
}

_BAD_REQUEST = {400: {"description": "Invalid password or settings"}}
_TOO_MANY = {429: {"description": "Too many failed attempts"}}
_WRONG_CURRENT = {403: {"description": "Current password is wrong"}}


class LoginBody(BaseModel):
    """Request body for logging in."""

    password: str


class SetupBody(BaseModel):
    """Request body for creating the admin password on first run."""

    setup_code: str
    password: str


class PasswordChangeBody(BaseModel):
    """Request body for changing the admin password."""

    current_password: str
    new_password: str


class AuthSettingsBody(BaseModel):
    """Request body for changing the auth mode."""

    mode: str
    trusted_proxies: str = ""
    proxy_header: str = "Remote-User"
    current_password: str = ""


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _headers(request: Request) -> dict[str, str]:
    return {k.lower(): v for k, v in request.headers.items()}


def install_auth_guard(app: FastAPI, auth: AuthManager) -> None:
    """Reject API calls without a valid login. The page and static files stay public:
    they contain no data, and the page needs to load to show the login form."""

    @app.middleware("http")
    async def guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if path.startswith("/api/") and path not in _PUBLIC_API:
            cookie = request.cookies.get(SESSION_COOKIE)
            if not auth.authenticated(_client(request), _headers(request), cookie):
                return JSONResponse({"detail": "Log in first."}, status_code=401)
        return await call_next(request)


def create_router(auth: AuthManager) -> APIRouter:
    """Return the router for /api/auth/*."""
    router = APIRouter()

    def start_session(request: Request, response: Response) -> None:
        secure = (
            request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "") == "https"
        )
        response.set_cookie(
            SESSION_COOKIE,
            auth.create_session(),
            max_age=SESSION_MAX_SECONDS,
            httponly=True,
            samesite="lax",
            secure=secure,
            path="/",
        )

    def require_current(password: str) -> None:
        if auth.password_set and not auth.check_password(password):
            raise HTTPException(status_code=403, detail="Current password is wrong.")

    @router.get("/auth/status")
    async def status(request: Request) -> dict:
        """What the GUI should show: setup form, login form, or the app."""
        cookie = request.cookies.get(SESSION_COOKIE)
        return {
            "mode": auth.mode,
            "setup_required": auth.mode == "password" and not auth.password_set,
            "authenticated": auth.authenticated(_client(request), _headers(request), cookie),
            "user": auth.proxy_user(_client(request), _headers(request))
            if auth.mode == "proxy"
            else "",
            "trusted_proxies": ",".join(str(n) for n in auth.trusted_proxies),
            "proxy_header": auth.proxy_header,
        }

    @router.post(
        "/auth/setup",
        responses={
            **_BAD_REQUEST,
            **_TOO_MANY,
            403: {"description": "Wrong setup code"},
            409: {"description": "An admin password already exists"},
        },
    )
    async def setup(body: SetupBody, request: Request, response: Response) -> dict:
        """Create the admin password; requires the setup code from the server logs."""
        client = _client(request)
        if auth.password_set:
            raise HTTPException(status_code=409, detail="An admin password already exists.")
        if wait := auth.locked_for(client):
            raise HTTPException(
                status_code=429, detail=f"Too many attempts. Try again in {wait // 60 + 1} min."
            )
        if not auth.check_setup_code(body.setup_code):
            auth.record_failure(client)
            raise HTTPException(
                status_code=403, detail="Wrong setup code. It is printed in the server logs."
            )
        try:
            await asyncio.to_thread(auth.set_password, body.password)
        except BackupError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        auth.record_success(client)
        start_session(request, response)
        return {"status": "ok"}

    @router.post("/auth/login", responses={**_TOO_MANY, 401: {"description": "Wrong password"}})
    async def login(body: LoginBody, request: Request, response: Response) -> dict:
        """Check the admin password and start a session."""
        client = _client(request)
        if wait := auth.locked_for(client):
            raise HTTPException(
                status_code=429, detail=f"Too many attempts. Try again in {wait // 60 + 1} min."
            )
        if not await asyncio.to_thread(auth.check_password, body.password):
            auth.record_failure(client)
            raise HTTPException(status_code=401, detail="Wrong password.")
        auth.record_success(client)
        start_session(request, response)
        return {"status": "ok"}

    @router.post("/auth/logout")
    async def logout(request: Request, response: Response) -> dict:
        """End this browser's session."""
        if cookie := request.cookies.get(SESSION_COOKIE):
            auth.end_session(cookie)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"status": "ok"}

    @router.post("/auth/password", responses={**_BAD_REQUEST, **_WRONG_CURRENT})
    async def change_password(
        body: PasswordChangeBody, request: Request, response: Response
    ) -> dict:
        """Change the admin password; every other session is signed out."""
        await asyncio.to_thread(require_current, body.current_password)
        try:
            await asyncio.to_thread(auth.set_password, body.new_password)
        except BackupError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        start_session(request, response)
        return {"status": "ok"}

    @router.put("/auth/settings", responses={**_BAD_REQUEST, **_WRONG_CURRENT})
    async def save_settings(body: AuthSettingsBody) -> dict:
        """Switch between password, proxy and off modes (needs the current password)."""
        await asyncio.to_thread(require_current, body.current_password)
        try:
            await asyncio.to_thread(
                auth.save_settings, body.mode, body.trusted_proxies, body.proxy_header
            )
        except BackupError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"status": "saved", "mode": auth.mode}

    return router
