# Spoolman

Worker type: `spoolman` · Method: REST API (no authentication)

## What's Backed Up

Spools (including archived ones), filaments, vendors, custom extra fields,
and settings.

## Setup

1. Set `SPOOLMAN_URL` in `.env`. Spoolman's API has no authentication, so
   nothing else is required.
2. Enable the `spoolman` service in `config/services.json`.

## `.env` Variables

| Variable          | Required | Notes                                        |
|---------------------|----------|---------------------------------------------------|
| `SPOOLMAN_URL`       | Yes      | Base URL, e.g. `https://spoolman.your-domain.com` |

## Restoring

This export is a **readable reference**, not a file the app can import directly. To rebuild, set up a fresh instance and re-create the records from the JSON (by hand, or with a short script against the same API endpoints the worker read from).
