# Immich

Worker type: `immich` · Method: REST API — **metadata only**

## What's Backed Up

The organisation layer that would be lost if you only restored the raw
library folder: albums and their asset IDs, people, tags, stacks, memories,
shared links, and per-asset metadata (EXIF, detected faces, favourites).

**Photo and video files themselves are NOT included.** Back those up
separately (filesystem snapshot, restic, etc.) — this worker only protects
the metadata layer that sits on top of them.

Exports only what the API key's user can see, so a non-admin key gives a
narrower backup than an admin key.

## Setup

1. Open Immich → **Account Settings → API Keys → New API Key**. Read
   permissions are sufficient.
2. Set the variables below in `.env`.
3. Enable the `immich` service in `config/services.json`.

## `.env` Variables

| Variable          | Required | Notes                                                  |
|---------------------|----------|-------------------------------------------------------|
| `IMMICH_URL`         | Yes      | Base URL, e.g. `https://immich.your-domain.com`       |
| `IMMICH_API_KEY`     | Yes      | API key from Account Settings → API Keys (read scope) |

## Restoring

This export is a **readable reference**, not a file the app can import directly. To rebuild, set up a fresh instance and re-create the records from the JSON (by hand, or with a short script against the same API endpoints the worker read from).

Album and people records reference Immich **asset IDs**. A re-uploaded library
gets new IDs, so this export works best for checking and rebuilding the
organisation layer, not for replaying it blindly.
