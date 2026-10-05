# OneDrive

After every successful backup, the file is uploaded to **Apps/Homelab Takeout**
in your OneDrive, one sub-folder per service. The oldest copies beyond
**Copies to keep at each destination** (Settings) are removed after each
upload; files you put there yourself are never touched.

The app only asks for `Files.ReadWrite.AppFolder`: it can use its own app
folder and nothing else in your OneDrive.

## Connect

1. In **Destinations → OneDrive**, click **Log in with Microsoft**.
2. Open `microsoft.com/devicelogin` on any device, enter the code shown, and
   sign in with your Microsoft account.
3. The dialog closes by itself once Microsoft confirms.
4. Switch on **Upload here** and click **Test connection**.

You stay connected as long as backups keep running: Microsoft refresh tokens
expire only after 90 days without use, and every upload renews them. The
token cache is stored with owner-only permissions and never shown in the GUI.

**Disconnect** deletes the tokens on this server. Microsoft doesn't let apps
of this kind revoke tokens themselves, so to cut access completely also remove
*Homelab Takeout* at <https://account.live.com/consent/Manage>.

**Work or school accounts** work when your organisation lets users approve
apps themselves. If you see "Need admin approval", ask your admin, or use your
own app registration (below).

## Advanced

- **Your own Microsoft client ID**: register an app in Microsoft Entra
  (*App registrations → New registration*), choose *Accounts in any
  organizational directory and personal Microsoft accounts*, turn on **Allow
  public client flows** under *Authentication*, and add the delegated Graph
  permission `Files.ReadWrite.AppFolder`. Paste the *Application (client) ID*.

## Restoring

Download the archive from *Apps/Homelab Takeout* in OneDrive, then follow the
**Restoring** section of that service's guide.
