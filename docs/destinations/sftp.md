# SFTP

Upload backups to any machine you can SSH into: a NAS (Synology, QNAP,
TrueNAS, Unraid), a VPS, or another server. Each service gets a sub-folder
under the remote folder you choose.

## Connect

1. In **Destinations → SFTP**, fill in **Host**, **Username**, **Remote
   folder** (an absolute path) and either a **Password** or an SSH key (paste a
   private key and click **Save key**).
2. Click **Test connection**. The first time, the app shows the server's host
   key fingerprint and asks you to trust it. To be sure it's really your
   server, compare it with the output of this command on the server:

   ```bash
   ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
   ```

3. Switch on **Upload backups here**.

## How it stays safe

- **Pinned host key.** Credentials are only sent after the server's key matches
  the one you trusted. If it ever changes, uploads stop with a clear error
  instead of logging in to a possible impostor. If you rebuilt the server,
  test the connection again and trust the new key.
- **Key or password, stored privately.** A pasted key is saved on this server
  with owner-only permissions; passwords live in the `.env` file (mode 600) and
  are never shown back in the GUI. An encrypted key works too: put its
  passphrase in **Password**.
- **No half-written files.** Each upload goes to a hidden `.partial` name first
  and is renamed into place only when complete.
- **Your files are safe.** Retention only removes files this app created
  (named `<service>_<date>_<time>...`).

## Settings

| Setting | Notes |
|---|---|
| Host, Port | Port defaults to 22 |
| Username | Needs write access to the remote folder |
| Password | Login password, or the key's passphrase when a key is set |
| Remote folder | Absolute path, created if missing |
| SSH key file *(advanced)* | Set automatically when you paste a key |
| Trusted host key *(advanced)* | Set when you confirm the fingerprint |
