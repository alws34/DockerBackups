# Home Assistant

Worker type: `homeassistant` · Method: websocket API (`backup/*` commands) + `/api/backup/download`

## What's Backed Up

A **native Home Assistant backup** — the same `.tar` you get from
Settings → System → Backups → *Backup now*:

- the Home Assistant configuration folder (YAML, `.storage`, integrations,
  dashboards, users, automations, scripts, …) and the history database;
- on **HA OS / Supervised** installs additionally all add-ons and the `share`,
  `ssl` and `addons/local` folders.

**Not included:** the `media` folder (it can be very large — back it up
separately if you need it). On Core/Container installs add-ons and folders
don't exist, so only configuration + database are backed up.

Each run:

1. asks HA to create a backup on its local storage (`backup/generate`),
2. polls `backup/info` every 5 s until HA reports it finished (gives up after 2 h),
3. downloads the `.tar` into the backup folder,
4. deletes that backup from HA again.

Step 4 is deliberate: HA's own retention ("keep N backups") only prunes
*automatic* backups, so backups created this way would otherwise pile up on
HA's disk forever. Your own automatic backups and their retention are never
touched.

### Encryption

The backup is encrypted with **your Home Assistant backup encryption key** —
the same key HA uses for its automatic backups. Find it in HA under
**Settings → System → Backups**, in the backup settings / configuration page
(*Encryption key* → download the **emergency kit**); see HA's
[backup emergency kit](https://www.home-assistant.io/more-info/backup-emergency-kit/)
page. Keep that key somewhere other than HA: without it the backup cannot be
restored.

If you never set up automatic backups (no key yet), or you turned off
encryption for HA's local storage, the backup is saved **unencrypted**. It then
contains secrets (`secrets.yaml`, access tokens), so treat the file like a
password. The run's log message says which one you got.

## Setup

1. Log in to Home Assistant as an **administrator** (the backup API is
   admin-only).
2. Click your user name (bottom left) → **Security** tab → **Long-lived access
   tokens → Create token**. Copy it — HA shows it only once.
3. Recommended: open **Settings → System → Backups** and set up automatic
   backups once, so HA creates an encryption key (see above).
4. Set the variables below in the GUI (or `.env`) and enable the service.

## `.env` Variables

| Variable              | Required | Notes                                                        |
|-----------------------|----------|--------------------------------------------------------------|
| `HOMEASSISTANT_URL`   | Yes      | Base URL, e.g. `http://homeassistant.local:8123`             |
| `HOMEASSISTANT_TOKEN` | Yes      | Long-lived access token of an admin user (Profile → Security) |

## Restoring

**Native restore, full.** The file is a normal Home Assistant backup:

- **Running instance:** Settings → System → Backups → ⋮ → **Upload backup**,
  pick the `.tar`, then open it and choose **Restore**. Enter the encryption key
  when asked.
- **Fresh install:** on the onboarding welcome screen choose **Upload backup**
  and pick the `.tar`.

Details: HA's [restoring a backup](https://www.home-assistant.io/common-tasks/general/#restoring-a-backup)
guide.

A backup from HA OS (with add-ons) restores fully only on HA OS / Supervised;
on Container you can restore the Home Assistant part of it.
