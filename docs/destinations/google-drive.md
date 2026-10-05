# Google Drive (Destination)

This is an upload **destination**, not a service worker — every successful
backup from any enabled service is uploaded to a Google Drive folder after
it's written locally.

## Setup

1. Create a Google Cloud project and enable the Drive API.
2. Create a service account and download its JSON key to
   `config/google-credentials.json`.
3. Share your target Drive folder with the service account's email address.
4. Set `GOOGLE_DRIVE_FOLDER_ID` in `.env`.
5. Set `destinations.google_drive.enabled: true` in `config/services.json`.
6. Uncomment the credentials volume mount in `docker-compose.yml`.

## `.env` Variables

| Variable                 | Required | Notes                                      |
|-----------------------------|----------|------------------------------------------------|
| `GOOGLE_DRIVE_FOLDER_ID`    | Yes (if enabled) | ID of the shared target folder         |

## Retention

`config/services.json → retention.drive_keep_count` limits how many backups
per service are kept on Drive; older ones are pruned after each successful
upload.
