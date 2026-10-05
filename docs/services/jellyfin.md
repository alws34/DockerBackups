# Jellyfin

Worker type: `jellyfin` · Method: REST API — **no media files**

## What's Backed Up

The data that lives only in Jellyfin's database and would be gone if it died:

- **Users** with their policies (admin, library access, parental limits) and
  their display/playback preferences. No passwords or password hashes.
- **Watch state per user**: every item that is played, a favourite,
  partly watched (resume position), liked or disliked, with play count, last
  played date and playback position.
- **Playlists**: each one with its sharing settings and its items in order.
- **Collections** (box sets) and their items.
- **Libraries**: names, content types, folder paths and library options.
- **Server settings**: general, network, encoding (transcoding) and branding
  configuration, plus server name and version. Fields that look like
  passwords, secrets or tokens are removed.
- **Installed plugins**: name, version and status.

Every exported item carries its provider IDs (IMDb, TMDb, TVDb, …) and its file
path, so it can be matched again on a rebuilt server, where item IDs differ.

**Not included:** media files, downloaded metadata and artwork, plugin
settings, API keys, devices, activity log and Live TV setup. Keep your media
backed up separately; metadata and artwork are re-downloaded by a library scan.

Needs Jellyfin 10.9 or newer.

## Setup

1. Open Jellyfin → **Dashboard → API Keys → +** and give the key a name
   such as `backup`. API keys have admin rights, which the export needs to read
   every user's watch state.
2. Set the variables below in `.env`.
3. Enable the `jellyfin` service in `config/services.json`.

## `.env` Variables

| Variable           | Required | Notes                                              |
|--------------------|----------|----------------------------------------------------|
| `JELLYFIN_URL`     | Yes      | Base URL, e.g. `https://jellyfin.your-domain.com`  |
| `JELLYFIN_API_KEY` | Yes      | API key from Dashboard → API Keys                  |

## Restoring

This export is a **readable reference**, not a file the app can import directly. To rebuild, set up a fresh instance and re-create the records from the JSON (by hand, or with a short script against the same API endpoints the worker read from).

In practice: re-create the users and libraries from `users.json` and
`libraries.json`, let the library scan finish, then replay watch state,
favourites, playlists and collections by matching each exported item to the
new library on its provider IDs or path (item IDs are new after a rebuild).
Users must set new passwords. Reinstall plugins from `plugins.json` and
re-enter their settings by hand.
