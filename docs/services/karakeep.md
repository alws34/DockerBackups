# Karakeep

Worker type: `karakeep` · Method: REST API

## What's Backed Up

Bookmarks (including their saved content), lists and their membership, tags,
and highlights — everything the authenticated user owns.

## Setup

1. Open Karakeep → **Settings → API Keys → New API Key**.
2. Set the variables below in `.env`.
3. Enable the `karakeep` service in `config/services.json`.

## `.env` Variables

| Variable             | Required | Notes                                        |
|------------------------|----------|---------------------------------------------------|
| `KARAKEEP_URL`         | Yes      | Base URL, e.g. `http://192.168.0.2:5010`          |
| `KARAKEEP_API_KEY`     | Yes      | API key from Settings → API Keys                  |

## Restoring

This export is a **readable reference**, not a file the app can import directly. To rebuild, set up a fresh instance and re-create the records from the JSON (by hand, or with a short script against the same API endpoints the worker read from).
