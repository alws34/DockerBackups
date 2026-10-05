# Vaultwarden

Worker type: `vaultwarden_encrypted_json` · Method: Bitwarden CLI (`bw`) → encrypted JSON export

## What's Backed Up

A full vault export (logins, notes, cards, identities, folders) in Bitwarden's
`encrypted_json` format. The export is encrypted at rest with its own password —
separate from your master password — so the backup file is safe even if it
leaks.

This worker does not touch Vaultwarden's database. It drives the official `bw`
CLI exactly as a user would: log in, unlock, sync, export.

## Setup

1. Open the Vaultwarden web vault → **Account Settings → Security → API Key**
   and copy the **Client ID** and **Client Secret**.
2. Set the variables below in `.env`.
3. Enable the `vaultwarden` service in `config/services.json`.

## `.env` Variables

| Variable                      | Required | Notes                                                    |
|--------------------------------|----------|-----------------------------------------------------------|
| `VAULTWARDEN_URL`              | Yes      | Base URL, e.g. `https://vault.your-domain.com`            |
| `BW_CLIENTID`                  | Yes      | From the API Key panel                                    |
| `BW_CLIENTSECRET`              | Yes      | From the API Key panel                                    |
| `BW_PASSWORD`                  | Yes      | Your vault master password — needed to unlock for export  |
| `VAULTWARDEN_EXPORT_PASSWORD`  | Yes      | Encrypts the export file. **Must differ from `BW_PASSWORD`.** |

## Notes

- The vault is always locked and logged out again after the run, even on failure.
- Nothing from this step is ever written to logs — commands containing secrets
  are redacted before logging (see [`threat-model.md`](../threat-model.md)).

## Restoring

**Native import.** In a fresh vault: **Tools → Import data**, format
**Bitwarden (json)**, select the file, and enter `VAULTWARDEN_EXPORT_PASSWORD`
when prompted. All items and folders come back. File attachments are not part
of any Bitwarden export, so keep those separately.
