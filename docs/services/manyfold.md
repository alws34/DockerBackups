# Manyfold

Worker type: `manyfold` · Method: Manyfold API v0 (OAuth2 client credentials) — **metadata only**

## What's Backed Up

The catalogue you built on top of your 3D files, as JSON files in one `.tar.gz`:

| File               | Contents                                                                 |
|--------------------|--------------------------------------------------------------------------|
| `creators.json`    | Every creator: name, slug, caption, notes, links                         |
| `collections.json` | Every collection: name, caption, notes, creator, parent collection, preview model, links |
| `models.json`      | Every model: name, caption, notes, tags, licence (SPDX), creator, collections, links, sensitive flag, preview file, and the list of its files (name and type) |

References between records are the Manyfold URLs in `@id` (for example a
model's `creator` and `isPartOf` collections).

**Not included:**

- **The 3D model files and images.** They live in your library folders; back
  those up separately (filesystem snapshot, restic, etc.).
- **Libraries** (folder paths and their settings), **lists**, comments,
  follows, users and site settings: the API does not expose them.
- Per-file details (file notes and captions, the presupported/unsupported
  pairing, print orientation). They need one request per file; the file
  *list* of each model is included.
- Creator groups (sharing permissions).

Only what the API key's owner may see is exported.

## Setup

Needs a Manyfold release with the API (added in 2025; the user menu then
has **Developer → API keys**). Your account must be at least a contributor.

1. In Manyfold open the user menu (top right) → **Developer → API keys →
   New API key**.
2. Give it a name, set the redirect URI to `urn:ietf:wg:oauth:2.0:oob`,
   select the **read** scope (nothing else is needed), keep **Confidential**
   ticked and save.
3. The key's page shows the **client ID** and **secret**. Add Manyfold in the
   web GUI and fill in the URL, client ID and secret (they are stored in `.env`).

Each backup trades the ID and secret for a short-lived access token
(`POST /oauth/token`) and only reads.

## `.env` Variables

| Variable                 | Required | Notes                                          |
|--------------------------|----------|------------------------------------------------|
| `MANYFOLD_URL`           | Yes      | e.g. `https://manyfold.example.com`            |
| `MANYFOLD_CLIENT_ID`     | Yes      | Client ID from the API key's page              |
| `MANYFOLD_CLIENT_SECRET` | Yes      | Secret from the same page                      |

## Restoring

This export is a **readable reference**, not a file Manyfold can import.

Restore your library folders first and let Manyfold scan them: it recreates
models from the files, but under new IDs and without your edits. Then
re-apply names, notes, tags, licences, links, creators and collections from
the JSON, by hand or with a short script against the same API (it has
create/update endpoints for creators, collections and models with a
`write`-scoped key). Match models by their file list (`hasPart` names), not
by `@id`, because a fresh scan hands out new IDs.
