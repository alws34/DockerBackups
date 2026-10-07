# Uptime Kuma

Worker type: `uptimekuma` · Method: Socket.IO API (the same one the web UI uses), username/password login

## What's Backed Up

Uptime Kuma has no REST API for its configuration, so the worker logs in over
Socket.IO like the web UI does and saves what the server sends:

| File                 | Contents                                                                 |
|----------------------|--------------------------------------------------------------------------|
| `monitors.json`      | Every monitor with its full settings, tags, notifications and parent group |
| `notifications.json` | Notification providers, including their webhook URLs and tokens          |
| `proxies.json`       | Proxies                                                                  |
| `status_pages.json`  | Each status page's settings plus its published monitor groups and pinned incident |
| `maintenance.json`   | Maintenance windows with the monitors and status pages they cover       |
| `tags.json`          | Tag definitions                                                          |
| `docker_hosts.json`  | Docker hosts used by Docker monitors                                     |
| `remote_browsers.json` | Remote browsers (2.x only)                                             |
| `api_keys.json`      | API key names, status and expiry (the server never sends the keys)      |
| `settings.json`      | General settings, minus anything named like a key, secret, password or token |
| `info.json`          | Server version and timezone                                              |

**Not included:** heartbeat and uptime history, incident history beyond the
pinned one, status page logos (only their URL), and the user account itself.

The worker only reads. It never sends a write event.

**The archive contains secrets.** Monitors keep their HTTP basic-auth
passwords, headers and database connection strings, and notifications keep
their bot tokens and webhook URLs, because you need them to rebuild. The
archive is written `chmod 600`; treat it like a password export.

Works with Uptime Kuma 1.23.x and 2.x (checked against the server source of
1.23.17 and 2.5.5).

## Setup

1. Use your Uptime Kuma login. Uptime Kuma has a single user account, so
   there is no separate read-only account to create.
2. **If 2FA is on**, the worker needs the 2FA secret to generate the code
   itself. Uptime Kuma only shows it while 2FA is being enabled: open
   **Settings → Security → Two Factor Authentication**, (disable and)
   enable 2FA, click **Show URI**, and copy the value after `secret=` in the
   `otpauth://` link into `UPTIMEKUMA_TOTP_SECRET`. Storing it next to the
   password means this machine can log in on its own, which is what 2FA is
   meant to prevent; if that's not acceptable, don't back up this instance
   with this worker. Each code is accepted once, so two runs less than 30
   seconds apart will fail the second time.
3. Set the variables below in `.env`.
4. Enable the `uptimekuma` service in `config/services.json`.

If the worker reaches Uptime Kuma through a reverse proxy, the proxy must
pass WebSocket upgrades (the web UI needs this too).

## `.env` Variables

| Variable                 | Required | Notes                                                       |
|--------------------------|----------|-------------------------------------------------------------|
| `UPTIMEKUMA_URL`         | Yes      | Base URL, e.g. `https://status.your-domain.com`             |
| `UPTIMEKUMA_USERNAME`    | Yes      | Your Uptime Kuma username                                   |
| `UPTIMEKUMA_PASSWORD`    | Yes      | Your Uptime Kuma password                                   |
| `UPTIMEKUMA_TOTP_SECRET` | No       | Only if 2FA is on: the `secret=` value from the 2FA setup URI |

## Restoring

This export is a **readable reference**, not a file the app can import directly.
Uptime Kuma 2.x has no import at all: the JSON backup/import was deprecated
in 1.x and removed in 2.0. To rebuild, set up a fresh instance and re-create
things in this order, from the JSON, by hand or with a script that drives
the same Socket.IO events the web UI uses (`addNotification`, `addProxy`,
`addTag`, `add`, `addStatusPage`/`saveStatusPage`, `addMaintenance`):

1. Notifications, proxies, docker hosts and remote browsers.
2. Tags.
3. Monitors: groups first, then the monitors in them. New IDs are assigned,
   so map the old IDs from `monitors.json` to the new ones as you go.
4. Status pages, using `publicGroupList` for which monitors go in which group.
5. Maintenance windows, then attach their monitors and status pages.
6. Settings (re-enter any key that was stripped, e.g. the Steam API key).

API keys can't be restored; create new ones and update whatever uses them.

On a 1.23.x instance, **Settings → Backup → Import** still accepts the old
JSON format (`version`, `notificationList`, `proxyList`, `monitorList`). It
may be possible to assemble such a file from `notifications.json`,
`proxies.json` and `monitors.json`, but this hasn't been tested.

Uptime Kuma's own data folder (`/app/data`, the SQLite/MariaDB database) is
the only complete backup, including history. This worker covers the case
where you want the configuration without stopping the container or reading
its files.
