# Registering the built-in login apps (maintainers)

The **Log in with Google / Microsoft** buttons use device-code logins with
client IDs that ship in [`app/destinations/oauth_apps.py`](../../app/destinations/oauth_apps.py).
They are registered once by the maintainer, so users never need a developer
console. Tokens go straight from Google or Microsoft to each user's own server;
nothing passes through the maintainer, and no domain or website is required.

Use a dedicated project email (not a personal one) as the support and contact
address: it is shown on the consent screens.

## Google ("TVs and Limited Input devices" client)

1. Create a project at <https://console.cloud.google.com/> named
   *Homelab Takeout* and enable the **Google Drive API**.
2. **Google Auth Platform → Branding**: app name *Homelab Takeout*, the
   project support email. Leave the app domain, homepage and privacy policy
   empty. Those are only needed for brand verification, which this app does
   not need.
3. **Audience**: user type **External**, then **Publish app** so the status is
   **In production**. In *Testing*, Google expires every login after 7 days.
4. **Data Access**: add only `https://www.googleapis.com/auth/drive.file`.
   It is a non-sensitive scope, so no verification or security assessment is
   required. Without brand verification the consent screen shows less
   branding, which is expected.
5. **Clients → Create client → TVs and Limited Input devices**. Copy the
   client ID and secret into `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`.

Google documents that installed-app client secrets are "obviously not treated
as a secret", which is why they can live in public source code.

## Microsoft (Entra public client)

1. At <https://entra.microsoft.com/> → **App registrations → New
   registration**: name *Homelab Takeout*, supported account types **Accounts
   in any organizational directory and personal Microsoft accounts**, no
   redirect URI.
2. **Authentication → Advanced settings → Allow public client flows: Yes**.
   Public clients have no secret.
3. **API permissions → Add → Microsoft Graph → Delegated →
   `Files.ReadWrite.AppFolder`**. Remove any other permissions. It needs no
   admin consent for personal accounts.
4. Copy the **Application (client) ID** into `MICROSOFT_CLIENT_ID`.

Work/school tenants that block user consent for unverified publishers will
show "Need admin approval". Publisher verification (a Microsoft partner
account) removes that, and is optional.

## After changing the IDs

Run the test suite, start the app, connect each provider once with a real
account, and run **Test connection** plus a backup to confirm uploads and
pruning.
