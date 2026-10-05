# Local folder

Copy every backup into another folder on the same machine, one sub-folder per
service. Use it to hand backups to something you already run:

- a NAS share mounted on the host (NFS/SMB via `/etc/fstab`)
- an [rclone](https://rclone.org/) mount, or a folder an rclone timer syncs to
  any of rclone's 70+ storage providers
- a Syncthing or Resilio folder
- a second disk

Homelab Takeout only writes the files; whatever moves that folder elsewhere is
yours to configure, using your own credentials.

## Connect

1. Set **Folder path** to an absolute path, e.g. `/mnt/nas/homelab-takeout`.
2. In Docker, mount that folder into the container at the same path, e.g. in
   `docker-compose.yml`:

   ```yaml
   volumes:
     - /mnt/nas/homelab-takeout:/mnt/nas/homelab-takeout
   ```

3. Click **Test connection**, then switch on **Upload backups here**.

**Without Docker (systemd install):** the service is sandboxed and can only
write to its own folders, so allow the destination folder once:

```bash
sudo systemctl edit homelab-takeout
# add, then save:
#   [Service]
#   ReadWritePaths=/mnt/nas/homelab-takeout
sudo systemctl restart homelab-takeout
```

## Example: sync the folder with rclone

```bash
# once: configure a remote interactively (rclone handles the provider's login)
rclone config

# then, e.g. from cron or a systemd timer:
rclone sync /mnt/nas/homelab-takeout my-remote:homelab-takeout
```

Retention in the local folder only removes files this app created (named
`<service>_<date>_<time>...`). With `rclone sync`, the remote then mirrors it.
