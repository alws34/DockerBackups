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
