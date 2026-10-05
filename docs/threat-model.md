# Security

## Threat Model

This agent runs on a private homelab network with access to service APIs and
credentials. The primary threats are:

1. **Secret leakage** — credentials escaping the container into logs, git, or disk.
2. **Backup file theft** — backup archives being read by unintended processes.
3. **Unauthorized GUI access** — someone else on the network driving the admin GUI.
4. **Dependency compromise** — a supply-chain attack via a Python package.

## Secret Handling

### Where secrets live

| Location                          | What                         | Git tracked? |
|-----------------------------------|------------------------------|-------------|
| `.env`                            | All API keys and passwords   | No          |
| `config/google-credentials.json`  | Google OAuth2 client secret  | No          |
| `config/google-tokens.json`       | Google OAuth2 access tokens  | No          |
| `config/services.json`            | Env var *names* (no values)  | Yes         |

### How secrets are protected in code

- **Never logged.** `worker.run_command()` accepts a `redacted_command` parameter.
  Any command that contains a secret (e.g. `bw unlock --password ...`) must pass
  a sanitized form for logging. The raw command is never written to any log.

- **Never in config files.** `services.json` stores the *name* of the env var
  (e.g. `"master_password_env": "BW_PASSWORD"`), not the value. Workers resolve
  the actual value at runtime via `require_env_by_option()`.

- **File permissions enforced by the app.** Every write to `.env` (via the GUI)
  calls `chmod 600` on the file. Backup output files are written with `chmod 600`.
  The app does not rely on umask alone.

- **Secrets masked in the GUI.** The `/api/env-vars/{type}` endpoint masks all
  `EnvVarSpec` entries where `secret=True` — values are returned as `"***"`.
  The frontend never receives the plaintext of a secret field after it is saved.

## File and Process Isolation

- **No Docker socket mounted.** The container cannot inspect or control other
  containers. Workers communicate with services over the network only.

- **No `root` writes to the host.** All persistent data (`backups/`, `state/`,
  `logs/`) is in explicitly mounted volumes. The container user can only write
  to those bind-mounted directories.

- **Subprocess argument lists, not shell strings.** All `subprocess.run()` calls
  use a `list[str]` command, never `shell=True`. This eliminates shell injection
  regardless of the content of env vars or config values.

## Web GUI Security

The GUI holds credentials for every backed-up service, so it is treated as an
admin panel (OWASP ASVS Level 2 controls):

- **Sign-in** (`app/api/auth.py`), mode chosen in Settings → Sign-in:
  - `password` (default): one admin password, hashed with scrypt
    (N=2^16, r=8, p=2). It is created on first start with a one-time setup code
    that is only written to the server logs and `state/setup-code.txt`, so
    nobody else on the network can claim the account by opening the page first.
  - `proxy`: an auth proxy (Authelia, Authentik, oauth2-proxy) signs users in;
    its user header is trusted **only** from `AUTH_TRUSTED_PROXIES`. uvicorn's
    `X-Forwarded-For` rewriting is off, so the client address can't be spoofed.
  - `off`: no sign-in, with a warning in the GUI. Only for networks nobody else
    can reach.
- **Sessions:** random 256-bit tokens in an `HttpOnly`, `SameSite=Lax` cookie
  (`Secure` behind HTTPS), stored server-side only as SHA-256 hashes; 7 days
  idle, 30 days absolute. Changing the password signs out every session.
  Restarting the app signs everyone out.
- **Brute force:** 5 wrong passwords lock that client out for 15 minutes.
- **DNS rebinding:** only Host headers that are IP addresses, `localhost`, or
  names in `ALLOWED_HOSTS` are served (`app/api/security.py`).
- **CSRF:** state-changing requests that the browser marks as cross-site
  (`Sec-Fetch-Site`, falling back to `Origin`) are rejected.
- **XSS:** everything the API returns is escaped before it reaches the page;
  `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: no-referrer` and a CSP
  with `frame-ancestors 'none'`. (A full `script-src` policy needs the GUI to
  drop its inline event handlers first.)
- **Secrets are write-only:** the API reports whether a secret is set, never
  its value.

Still recommended: keep the GUI on your LAN or behind a VPN (Tailscale,
WireGuard) rather than exposing it to the internet.

**Recovery:** delete `state/auth.json` and restart to get a new setup code;
set `AUTH_MODE=password` in `.env` if a proxy setting locked you out.

## Backup File Security

- Vaultwarden exports are **encrypted** with `VAULTWARDEN_EXPORT_PASSWORD` before
  writing to disk. Even if the backup file is exfiltrated, it requires the export
  password to decrypt. This password must differ from the master password.

- All other backups (wikijs, snipeit, etc.) are unencrypted archives. Protect the
  `backups/` volume directory with appropriate host-level permissions.

- Do not store backups on the same host as the services they back up. Use Google
  Drive upload or copy `backups/` to a separate machine.

## Dependency Security

- All Python dependencies are pinned to exact versions **and sha256 hashes** in
  `requirements.txt` / `requirements-dev.txt` (generated by `pip-compile
  --generate-hashes`), and installed with `pip install --require-hashes`. A
  tampered or substituted package fails the install. Dependabot proposes
  upgrades weekly; nothing floats silently.

- The base image is pinned by digest, and GitHub Actions are pinned by commit SHA.

- `ruff` with the `S` (bandit) rule set is enabled. CI should run `ruff check`
  on every pull request to catch common security anti-patterns.

- Audit dependencies periodically:
  ```bash
  pip install pip-audit
  pip-audit -r requirements.txt
  ```

## What This Agent Does NOT Do

- Does not exfiltrate telemetry, usage data, or logs to any external service.
- Does not open outbound connections except to the configured service URLs and
  Google Drive (if enabled).
- Does not listen on any port other than `$WEB_PORT` (default 8080).
- Does not run with elevated privileges inside the container: it runs as an
  unprivileged user (uid 1000 by default) with all capabilities dropped,
  `no-new-privileges`, and a read-only root filesystem (see `docker-compose.yml`).

## Reporting Security Issues

Please report vulnerabilities privately, not in a public issue. See
[`SECURITY.md`](../SECURITY.md).
