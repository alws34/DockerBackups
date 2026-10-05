# AdGuard Home

Worker type: `adguardhome` · Method: REST API (`/control`, HTTP basic auth)

## What's Backed Up

DNS settings, filter lists and user rules, rewrites, clients, blocked
services, access lists, DHCP status, TLS config, and the query-log/stats/
safe-browsing/parental/safe-search config blocks.

## Setup

1. Use an existing AdGuard Home web UI username and password — the
   `/control` API authenticates with the same basic-auth credentials as the
   dashboard.
2. Set the variables below in `.env`.
3. Enable the `adguardhome` service in `config/services.json`.

## `.env` Variables

| Variable             | Required | Notes                                       |
|-------------------------|----------|-----------------------------------------------|
| `ADGUARD_URL`           | Yes      | Web UI base URL, e.g. `http://127.0.0.1:3000` |
| `ADGUARD_USERNAME`      | Yes      | AdGuard Home web UI username                   |
| `ADGUARD_PASSWORD`      | Yes      | AdGuard Home web UI password                   |

## Restoring

This export is a **readable reference**, not a file the app can import directly. To rebuild, set up a fresh instance and re-create the records from the JSON (by hand, or with a short script against the same API endpoints the worker read from).

Most blocks map 1:1 onto AdGuard Home's settings pages and its
`/control/*` API endpoints.
