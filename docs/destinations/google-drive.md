# Google Drive

After every successful backup, the file is uploaded to a **Homelab Takeout**
folder in your Google Drive, one sub-folder per service. The oldest copies
beyond **Copies to keep at each destination** (Settings) are removed after each
upload. Files you put there yourself are never touched.

The app only asks for the `drive.file` permission: it can see and change only
files and folders it created itself, never the rest of your Drive.

## Connect

1. In **Destinations → Google Drive**, click **Log in with Google**.
2. A short code appears. Open `google.com/device` on any device (your phone is
   fine), enter the code, and sign in with your Google account.
3. The dialog closes by itself once Google confirms. The card shows
   **Connected as you@gmail.com**.
4. Switch on **Upload here** and click **Test connection**.

You stay connected: the app keeps a refresh token (stored with owner-only
permissions, never shown in the GUI) and gets new access tokens by itself.
**Disconnect** revokes the token at Google and deletes it locally. You can
also remove access any time at <https://myaccount.google.com/permissions>.

This works on a headless server because the sign-in happens on Google's own
page on any device. No redirect address, public hostname or domain is needed.

## Advanced

- **Drive folder ID**: upload into a specific folder instead of
  *Homelab Takeout*. Because of `drive.file`, it must be a folder this app
  created (or one you connected while using your own client below).
- **Your own device-login client**: if you'd rather not use the built-in app,
  create a Google Cloud OAuth client of type **TVs and Limited Input devices**,
  enable the Drive API, publish the consent screen to **In production** (in
  *Testing*, Google expires logins after 7 days), and paste its ID and secret.
- **Use your own Google OAuth client** (the older method): upload a
  `client_secret.json`, add the shown redirect URI to your client, and click
  **Authorize with my client**. Google only accepts `localhost` or HTTPS
  redirect URIs, so this works when you open the GUI on the server itself or
  through an HTTPS reverse proxy.

## Restoring

Download the archive from the *Homelab Takeout* folder in Drive, then follow
the **Restoring** section of that service's guide.
