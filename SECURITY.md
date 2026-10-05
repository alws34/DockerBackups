# Security Policy

## Supported versions

Only the **latest release** (and `main`) receives security fixes. Homelab Takeout
is built from source, so upgrading is `git pull` (or checking out the new tag)
followed by `docker compose up -d --build`.

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Report privately through GitHub's private vulnerability reporting:
go to the repository's **Security** tab and click **Report a vulnerability**
(<https://github.com/alws34/homelab-takeout/security/advisories/new>).

Include what you can: affected version or commit, a description of the issue and
its impact, and steps or a proof of concept to reproduce it.

## What to expect

- **Acknowledgement within 7 days.**
- An initial assessment (accepted or declined, with reasoning) within 14 days.
- For accepted reports, a fix in a new release as soon as practical, normally
  within 90 days. Coordinated disclosure: the advisory is published when the fix
  is released, or at 90 days, whichever comes first, unless we agree otherwise.
- Credit in the advisory if you want it.

This is a one-maintainer project, so timelines are best effort, but reports are
taken seriously.

## Scope

In scope:

- The application code in this repository (`app/`, the web GUI and its API)
- The `Dockerfile`, `docker-compose.yml`, `entrypoint.sh` and other install or
  build scripts in this repository
- The release process (signed source tarballs, provenance)

Out of scope:

- The third-party apps Homelab Takeout talks to (Vaultwarden, Immich, n8n, ...):
  report those to their own projects
- Running the web GUI exposed to an untrusted network without an auth proxy; it
  has no built-in authentication by design (see [`docs/threat-model.md`](docs/threat-model.md))

## Verifying releases

Release tarballs are signed with Sigstore and carry SLSA build provenance. See
[`docs/VERIFYING_RELEASES.md`](docs/VERIFYING_RELEASES.md).
