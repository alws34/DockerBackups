# SMB share

Upload backups to a Windows, Samba or NAS share (Synology, QNAP, TrueNAS,
Unraid, a Windows PC). The app speaks SMB2/3 directly, so nothing has to be
mounted on the host or inside the container.

## Connect

1. On your NAS, create (or pick) a share and a user with write access to it.
2. In **Destinations → SMB share**, fill in **Server**, **Share**, an optional
   **Folder in share**, **Username** and **Password**.
3. Click **Test connection**, then switch on **Upload backups here**.

Each service gets a sub-folder: `\\server\share\<folder>\<service>\`.

## Notes

- **Encryption is on by default** (SMB3). Very old servers that only speak
  SMB2 need **Encrypt traffic** set to `false` under *Advanced*.
- The password lives in the `.env` file (mode 600) and is never shown back in
  the GUI.
- Uploads go to a hidden `.partial` file first and are renamed into place when
  complete, so a dropped connection never leaves a truncated backup.
- Retention only removes files this app created (named
  `<service>_<date>_<time>...`).
