# Verifying a release

Every `v*` tag produces a GitHub Release built by
[`.github/workflows/build-release.yml`](../.github/workflows/build-release.yml),
a reusable workflow that GitHub runs in isolation from the calling workflow. Each
release has four assets:

| Asset | What it is |
|---|---|
| `homelab-takeout-X.Y.Z.tar.gz` | Source tarball (`git archive` of the tagged commit) |
| `homelab-takeout-X.Y.Z.tar.gz.sigstore.json` | Keyless Sigstore signature bundle (cosign) |
| `homelab-takeout-X.Y.Z.tar.gz.intoto.jsonl` | SLSA build provenance attestation (also stored on GitHub) |
| `homelab-takeout-X.Y.Z.spdx.json` | SBOM of the source tree (SPDX JSON) |

There is no prebuilt container image. You build it yourself from the verified source.

## 1. Download

```bash
VERSION=1.2.3   # the release you want, without the leading "v"
gh release download "v$VERSION" -R alws34/homelab-takeout
```

## 2. Verify the build provenance (GitHub CLI)

```bash
gh attestation verify "homelab-takeout-$VERSION.tar.gz" \
  -R alws34/homelab-takeout \
  --signer-workflow alws34/homelab-takeout/.github/workflows/build-release.yml \
  --source-ref "refs/tags/v$VERSION"
```

This checks that the tarball's sha256 matches an attestation signed by
`build-release.yml` in this repository, for that tag. To check against the
downloaded attestation instead of fetching it from GitHub, add
`--bundle "homelab-takeout-$VERSION.tar.gz.intoto.jsonl"`.

## 3. Verify the Sigstore signature (cosign)

```bash
cosign verify-blob \
  --bundle "homelab-takeout-$VERSION.tar.gz.sigstore.json" \
  --certificate-identity-regexp '^https://github.com/alws34/homelab-takeout/.github/workflows/build-release.yml@refs/tags/v' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  "homelab-takeout-$VERSION.tar.gz"
```

To pin the exact tag instead of any `v*` tag, use
`--certificate-identity "https://github.com/alws34/homelab-takeout/.github/workflows/build-release.yml@refs/tags/v$VERSION"`.

Both commands must print a successful verification. If either fails, do not use
the tarball.

## 4. Build from the verified tarball

```bash
tar -xzf "homelab-takeout-$VERSION.tar.gz"
cd "homelab-takeout-$VERSION"
cp .env.example .env && chmod 600 .env
mkdir -p backups logs state && chmod 700 backups logs state
docker compose up -d --build
```

To upgrade an existing install, extract the new tarball next to the old one and
move your `.env`, `config/`, `backups/`, `logs/` and `state/` across (or copy the
new files over the old checkout), then run `docker compose up -d --build`.

## Optional: reproduce the tarball

The tarball is a plain `git archive` of the tag, so you can rebuild it and
compare checksums (gzip output can differ between git versions; the extracted
files will always match):

```bash
git clone https://github.com/alws34/homelab-takeout.git && cd homelab-takeout
git archive --format=tar.gz --prefix="homelab-takeout-$VERSION/" "v$VERSION" | sha256sum
```
