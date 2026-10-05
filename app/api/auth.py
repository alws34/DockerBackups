"""GUI authentication: admin password, sessions, lockout, and the reverse-proxy mode.

Three modes, chosen in Settings (stored as AUTH_MODE in .env):

- ``password`` (default): one admin password, set on first run with a one-time setup
  code that is only printed to the server's logs, so nobody else on the network can
  claim the admin account by opening the page first.
- ``proxy``: an auth proxy (Authelia, Authentik, oauth2-proxy) already logged the user
  in; trust its user header, but only from the proxy IPs listed in AUTH_TRUSTED_PROXIES.
- ``off``: no login. Only for a GUI that is never reachable by anyone else.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from app.core.context import BackupError
from app.core.env_manager import EnvManager
from app.destinations.base import write_private

logger = logging.getLogger(__name__)

MODES = ("password", "proxy", "off")
MIN_PASSWORD_LENGTH = 12
SESSION_COOKIE = "ht_session"
SESSION_IDLE_SECONDS = 7 * 24 * 3600
SESSION_MAX_SECONDS = 30 * 24 * 3600
MAX_FAILURES = 5
LOCKOUT_SECONDS = 15 * 60

# OWASP Password Storage Cheat Sheet: scrypt N=2^16, r=8, p=2 (64 MiB per check).
_SCRYPT = {"n": 2**16, "r": 8, "p": 2, "maxmem": 128 * 1024 * 1024, "dklen": 32}


def hash_password(password: str) -> str:
    """Return ``scrypt$<salt>$<hash>`` for storage."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check of ``password`` against a ``hash_password`` value."""
    try:
        scheme, salt_b64, digest_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), **_SCRYPT)
        return hmac.compare_digest(digest, base64.b64decode(digest_b64))
    except ValueError:
        return False


@dataclass
class _Session:
    created: float
    last_seen: float


class AuthManager:
    """Holds the auth settings, the admin password hash and the live sessions."""

    def __init__(
        self, state_root: Path, env_manager: EnvManager | None, env: dict[str, str]
    ) -> None:
        self._file = state_root / "auth.json"
        self._setup_file = state_root / "setup-code.txt"
        self._env_manager = env_manager
        self._lock = threading.Lock()
        self._sessions: dict[str, _Session] = {}
        self._failures: dict[str, tuple[int, float]] = {}
        self._password_hash = self._load_hash()
        self.apply_settings(
            env.get("AUTH_MODE", "password"),
            env.get("AUTH_TRUSTED_PROXIES", ""),
            env.get("AUTH_PROXY_HEADER", "Remote-User"),
        )
        self.setup_code = ""
        if self.mode == "password" and not self._password_hash:
            self._new_setup_code()

    # ── settings ──────────────────────────────────────────────────────────

    def apply_settings(self, mode: str, trusted_proxies: str, proxy_header: str) -> None:
        """Validate and activate auth settings (raises ``BackupError`` if invalid)."""
        mode = (mode or "password").strip().lower()
        if mode not in MODES:
            raise BackupError(f"AUTH_MODE must be one of {', '.join(MODES)}, got '{mode}'.")
        try:
            networks = [
                ipaddress.ip_network(p.strip(), strict=False)
                for p in trusted_proxies.split(",")
                if p.strip()
            ]
        except ValueError as e:
            raise BackupError(f"Invalid trusted proxy address: {e}") from e
        if mode == "proxy" and not networks:
            raise BackupError("Proxy mode needs at least one trusted proxy IP or network.")
        header = (proxy_header or "Remote-User").strip()
        self.mode, self.trusted_proxies, self.proxy_header = mode, networks, header

    def save_settings(self, mode: str, trusted_proxies: str, proxy_header: str) -> None:
        """Apply settings and persist them to .env."""
        self.apply_settings(mode, trusted_proxies, proxy_header)
        if self._env_manager is not None:
            self._env_manager.update(
                {
                    "AUTH_MODE": self.mode,
                    "AUTH_TRUSTED_PROXIES": ",".join(str(n) for n in self.trusted_proxies),
                    "AUTH_PROXY_HEADER": self.proxy_header,
                }
            )
        if self.mode == "password" and not self._password_hash:
            self._new_setup_code()
        logger.warning(f"GUI auth mode set to '{self.mode}'")

    # ── password ──────────────────────────────────────────────────────────

    def _load_hash(self) -> str:
        try:
            return json.loads(self._file.read_text()).get("password_hash", "")
        except (OSError, ValueError):
            return ""

    def _new_setup_code(self) -> None:
        self.setup_code = "-".join(secrets.token_hex(2).upper() for _ in range(3))
        write_private(self._setup_file, self.setup_code + "\n")
        logger.warning(
            f"No admin password yet. Open the web GUI and enter setup code {self.setup_code} "
            f"(also saved in {self._setup_file})."
        )

    @property
    def password_set(self) -> bool:
        return bool(self._password_hash)

    def set_password(self, password: str) -> None:
        """Store a new admin password and end every existing session."""
        if len(password) < MIN_PASSWORD_LENGTH:
            raise BackupError(f"Use at least {MIN_PASSWORD_LENGTH} characters.")
        self._password_hash = hash_password(password)
        write_private(self._file, json.dumps({"password_hash": self._password_hash}))
        self._setup_file.unlink(missing_ok=True)
        self.setup_code = ""
        with self._lock:
            self._sessions.clear()

    def check_password(self, password: str) -> bool:
        return bool(self._password_hash) and verify_password(password, self._password_hash)

    def check_setup_code(self, code: str) -> bool:
        return bool(self.setup_code) and hmac.compare_digest(code.strip().upper(), self.setup_code)

    # ── lockout ───────────────────────────────────────────────────────────

    def locked_for(self, client: str) -> int:
        """Seconds this client must still wait before trying again (0 = may try)."""
        count, until = self._failures.get(client, (0, 0.0))
        return max(int(until - time.monotonic()), 0) if count >= MAX_FAILURES else 0

    def record_failure(self, client: str) -> None:
        count, _ = self._failures.get(client, (0, 0.0))
        count += 1
        self._failures[client] = (count, time.monotonic() + LOCKOUT_SECONDS)
        logger.warning(f"Failed GUI login from {client} ({count} in a row)")

    def record_success(self, client: str) -> None:
        self._failures.pop(client, None)
        logger.info(f"GUI login from {client}")

    # ── sessions ──────────────────────────────────────────────────────────

    def create_session(self) -> str:
        token = secrets.token_urlsafe(32)
        now = time.monotonic()
        with self._lock:
            self._sessions[hashlib.sha256(token.encode()).hexdigest()] = _Session(now, now)
        return token

    def end_session(self, token: str) -> None:
        with self._lock:
            self._sessions.pop(hashlib.sha256(token.encode()).hexdigest(), None)

    def session_valid(self, token: str | None) -> bool:
        if not token:
            return False
        key = hashlib.sha256(token.encode()).hexdigest()
        now = time.monotonic()
        with self._lock:
            session = self._sessions.get(key)
            if session is None:
                return False
            if (
                now - session.last_seen > SESSION_IDLE_SECONDS
                or now - session.created > SESSION_MAX_SECONDS
            ):
                del self._sessions[key]
                return False
            session.last_seen = now
            return True

    # ── per-request decision ──────────────────────────────────────────────

    def proxy_user(self, client_ip: str, headers: dict[str, str]) -> str:
        """The proxy-asserted user, or "" if the request didn't come from a trusted proxy."""
        try:
            ip = ipaddress.ip_address(client_ip)
        except ValueError:
            return ""
        if not any(ip in net for net in self.trusted_proxies):
            return ""
        return headers.get(self.proxy_header.lower(), "").strip()

    def authenticated(self, client_ip: str, headers: dict[str, str], cookie: str | None) -> bool:
        if self.mode == "off":
            return True
        if self.mode == "proxy":
            return bool(self.proxy_user(client_ip, headers))
        return self.session_valid(cookie)
