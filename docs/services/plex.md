# Plex

Worker type: `plex` · Method: local Plex Media Server API (`X-Plex-Token`, JSON)

## What's Backed Up

What you would lose if Plex's database died, as JSON files in one `.tar.gz`:

| File                    | Contents                                                                 |
|-------------------------|--------------------------------------------------------------------------|
| `server.json`           | Server name, version, machine identifier                                 |
| `sections.json`         | Libraries: type, agent, scanner, language, folder locations              |
| `section_settings.json` | Each library's advanced settings                                         |
| `items.json`            | Every item per library, with watch state (`viewCount`, `viewOffset`, `lastViewedAt`), `userRating`, `addedAt`, and match identifiers (`ratingKey`, `guid` plus the `Guid` list of IMDb/TMDB/TVDB IDs, title, year, file paths under `Media` → `Part`). TV libraries include shows **and every episode** (watch state lives on episodes); music libraries include artists, albums and tracks. |
| `collections.json`      | Collections per library                                                  |
| `collection_items.json` | The items in each collection                                             |
| `playlists.json`        | Playlists of the token owner                                             |
| `playlist_items.json`   | The items in each playlist, in order                                     |
| `history.json`          | Full watch history, newest first, with `accountID`                       |
| `accounts.json`         | Server accounts, to map `accountID` in the history to a name             |
| `prefs.json`            | Server settings, **minus** anything secret (tokens, passwords, certificate key) |

**Not included:**

- **Media files.** Back up your movies, shows and music separately.
- Posters, artwork, custom thumbnails, and Plex's metadata/transcoder caches.
- **Other users' watch state, ratings and playlists.** Plex keeps these per
  user and the API returns them for the token's owner only. Managed (Home)
  users and friends have their own tokens; add a second Plex instance with
  that user's token if you want theirs too. The watch history is the
  exception: the owner's token sees everyone's history.

## Setup

1. Find the server owner's token (Plex's guide:
   [Finding an authentication token / X-Plex-Token](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/)):
   sign in to Plex Web as the owner, open any movie or episode, click **⋯ →
   Get Info → View XML**, and copy the value after `X-Plex-Token=` in the
   address bar.
2. Add Plex in the web GUI and fill in the URL and token (they are stored in
   `.env`).
3. Use the server's local address, e.g. `http://plex.lan:32400`. If
   **Settings → Network → Secure connections** is set to *Required*, plain
   `http` is refused; use your `https://…plex.direct:32400` address instead
   (a bare `https://<ip>:32400` fails certificate checks).

The token is as powerful as your Plex password; the worker only ever reads.
Signing out of all devices in your Plex account invalidates it, and the next
backup will fail with a 401 until you paste a new one.

## `.env` Variables

| Variable     | Required | Notes                                                   |
|--------------|----------|---------------------------------------------------------|
| `PLEX_URL`   | Yes      | Server URL including the port, e.g. `http://plex.lan:32400` |
| `PLEX_TOKEN` | Yes      | Server owner's `X-Plex-Token` (see Setup)               |

## Restoring

This export is a **readable reference**, not a file Plex can import. There is
no API to load it back in one go.

To rebuild after losing the database: set up Plex, add the libraries again
using `sections.json` and `section_settings.json`, and let it scan. Then
re-apply what matters from the JSON, by hand or with a short script against
the same API (for example with [python-plexapi](https://python-plexapi.readthedocs.io/)):
match each item by its `Guid` IDs (IMDb/TMDB/TVDB) or file path, never by
`ratingKey` (a new database hands out new ones), and then mark it watched,
set the resume point, set the rating, and re-create collections and
playlists. History entries cannot be written back; they stay a record of
what was watched when.

If you still have Plex's own database backups (Settings → Scheduled Tasks →
*Backup database every three days*), restoring one of those is more complete
than this export.
