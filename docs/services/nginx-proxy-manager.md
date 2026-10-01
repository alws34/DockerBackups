# Nginx Proxy Manager

Worker type: `nginx_proxy_manager` · Method: REST API (token login with user credentials)

## What's Backed Up

Proxy hosts, redirection hosts, 404 (dead) hosts, streams, access lists,
certificate **metadata** (not private keys), users, and settings.

Certificate private keys are never exported — only the certificate records
NPM's API exposes (domains, expiry, provider). Re-issue or restore keys
separately.

## Setup

1. Use an existing NPM admin account's email and password — NPM's API
   authenticates the same way as the admin UI (no separate API token).
2. Set the variables below in `.env`.
3. Enable the `nginx_proxy_manager` service in `config/services.json`.

## `.env` Variables

| Variable       | Required | Notes                                   |
|------------------|----------|----------------------------------------------|
| `NPM_URL`        | Yes      | Admin UI base URL, e.g. `http://127.0.0.1:81` |
| `NPM_EMAIL`      | Yes      | Login email of an NPM admin user             |
| `NPM_PASSWORD`   | Yes      | Password for that user                        |
