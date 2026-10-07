# qBittorrent

Worker type: `qbittorrent` · Method: WebUI API v2 (login with username/password, SID cookie)

## What's Backed Up

- App version, WebUI API version and build info
- The torrent list (`torrents/info`) and, per torrent, its properties (save
  path, limits, share ratio…), trackers and file list (with priorities)
- The `.torrent` file of every torrent (`torrents/<hash>.torrent` in the
  archive; needs qBittorrent 4.5 or newer)
- Categories (with their save paths) and tags
- RSS feeds and RSS auto-download rules
- App settings (`app/preferences`) with secrets removed

**Not backed up:** the downloaded content itself (the files on disk), RSS
article history, logs and transfer statistics.

### Settings that are removed

Any preference whose name contains `password`, `passwd`, `secret`, `token`,
`api_key`/`apikey`, `salt` or ends in `_hash` is dropped. On current versions
that is `web_ui_password` (qBittorrent doesn't return it anyway),
`proxy_password`, `mail_notification_password` and `dyndns_password`, plus
any API key field newer versions add. The archive's `notes.json` lists the
exact keys removed in each backup. Re-enter these by hand after a restore.

### Archive layout

```
qbittorrent_YYYYMMDD_HHMMSS/
  version.json  torrents.json  torrent_details.json  categories.json
  tags.json  rss_items.json  rss_rules.json  preferences.json  notes.json
  torrents/<hash>.torrent
```

`notes.json` also lists torrents whose `.torrent` file couldn't be exported:
`HTTP 409` means a magnet link that hasn't fetched its metadata yet,
`HTTP 404` means qBittorrent is older than 4.5 (or the torrent was removed
during the backup).

## Setup

1. In qBittorrent, open **Tools → Options → Web UI** and tick **Web User
   Interface (Remote control)**. Note the port.
2. Under **Authentication** set a username and password. Don't tick "Bypass
   authentication for clients on localhost / in whitelisted subnets" for the
   Homelab Takeout address unless you mean to; the login still works either
   way.
3. If **Enable Host header validation** is on, make sure the address you put
   in `QBITTORRENT_URL` is in the allowed domain list.
4. Set the variables below (GUI or `.env`) and enable the service.

Running two qBittorrent instances? Add a second one from the app picker; each
gets its own URL, username and password.

## `.env` Variables

| Variable               | Required | Notes                                               |
|------------------------|----------|-----------------------------------------------------|
| `QBITTORRENT_URL`      | Yes      | WebUI address, e.g. `http://192.168.0.2:8080`       |
| `QBITTORRENT_USERNAME` | Yes      | Options → Web UI → Authentication → Username        |
| `QBITTORRENT_PASSWORD` | Yes      | Options → Web UI → Authentication → Password        |

Too many failed logins make qBittorrent ban the IP for a while (Options →
Web UI → "ban client after consecutive failures"). If backups fail with
"Login blocked (HTTP 403)", fix the password and wait for the ban to expire
or restart qBittorrent.

## Restoring

The archive is a **reference plus the original `.torrent` files**, not
something qBittorrent imports in one go. To rebuild a client:

1. Put the downloaded data back where it was (it's not in this backup).
2. Re-create categories from `categories.json` (Categories panel → Add,
   using the saved `savePath`) and tags from `tags.json`.
3. Re-apply the settings you need from `preferences.json` (Tools → Options)
   and re-enter the removed passwords.
4. Add each `torrents/<hash>.torrent` back. Look the hash up in
   `torrents.json` / `torrent_details.json` for its `category`, `tags` and
   `save_path`, and set them in the add dialog (untick "Start torrent" if you
   want to recheck first). qBittorrent will recheck existing data and resume
   seeding.

   For many torrents, script it against the same API, e.g. one call per hash
   to `POST /api/v2/torrents/add` with `torrents=@<hash>.torrent`,
   `savepath`, `category` and `tags`.
5. Re-add RSS feeds from `rss_items.json` and rules from `rss_rules.json`
   (RSS tab, or `rss/addFeed` and `rss/setRule` with the rule JSON as-is).
