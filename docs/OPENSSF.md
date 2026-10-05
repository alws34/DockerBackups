# OpenSSF / supply-chain checklist

What the repository itself now provides, and what the maintainer has to switch on
in GitHub or on external sites. Targets: OpenSSF Scorecard 7+, OpenSSF Best
Practices **passing**, OSPS Baseline **level 1**, SLSA Build **L3** for source
releases, CIS Docker Benchmark container items.

## Covered in the repository

| Area | Where |
|---|---|
| Hash-pinned Python deps (runtime + dev), `--require-hashes` installs | `requirements*.in/.txt`, `Dockerfile`, `ci.yml` |
| Locked Bitwarden CLI (integrity hashes, `npm ci`) | `bw/package.json`, `bw/package-lock.json` |
| Base image pinned by digest | `Dockerfile` |
| All actions pinned by commit SHA, `contents: read` by default, write scopes per job, no `pull_request_target`, no `github.event.*` in `run:` | `.github/workflows/*` |
| Dependency update tool (pip, npm, docker, actions; weekly, grouped) | `.github/dependabot.yml` |
| CI: ruff, tests + coverage, Docker build and smoke test on amd64 + arm64 | `ci.yml` |
| SAST: CodeQL (Python + Actions), ruff bandit rules, SonarQube Cloud (when token set) | `codeql.yml`, `ci.yml`, `sonar-project.properties` |
| Scorecard with published results and SARIF upload | `scorecard.yml` |
| Signed source releases: Sigstore bundle, SLSA provenance (`.intoto.jsonl`), SPDX SBOM, built in an isolated reusable workflow | `release.yml`, `build-release.yml`, `docs/VERIFYING_RELEASES.md` |
| Security policy and private reporting path | `SECURITY.md`, `docs/threat-model.md` |
| Contribution guide, code of conduct, code owners | `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `.github/CODEOWNERS` |
| OSI license, included in every source tarball | `LICENSE` (MIT) |
| Container: non-root user, `HEALTHCHECK`, no compiler in image, read-only rootfs, `cap_drop: ALL`, `no-new-privileges`, no published image | `Dockerfile`, `docker-compose.yml` |

`network_mode: host` is kept on purpose (workers must reach LAN services), so the
CIS item about host networking is an accepted, documented deviation.

## Manual steps (GitHub settings and external sites)

Do these roughly in order.

1. **Rename the repository** from `DockerBackups` to `homelab-takeout`
   (Settings → General) **before the first signed release**. Signatures and
   provenance embed the repository name at signing time, and the commands in
   `docs/VERIFYING_RELEASES.md` use `alws34/homelab-takeout`. GitHub redirects
   the old URL.
2. **Account 2FA**: enable two-factor authentication on the `alws34` account
   (a passkey or security key is best).
3. **Private vulnerability reporting**: Settings → Code security → enable
   *Private vulnerability reporting* (`SECURITY.md` and `CODE_OF_CONDUCT.md` point there).
4. **Secret scanning + push protection**: Settings → Code security → enable
   both (currently disabled).
5. **Dependabot alerts** and **Dependabot security updates**: enable in the same
   page (version updates already come from `dependabot.yml`).
6. **Code scanning**: after `codeql.yml` and `scorecard.yml` first run, results
   show under Security → Code scanning. If GitHub's "default setup" for CodeQL is
   on, switch it off so it doesn't conflict with the advanced workflow.
7. **Ruleset for `main`** (Settings → Rules → Rulesets → New branch ruleset,
   target the default branch, enforcement *Active*):
   - Restrict deletions; Block force pushes
   - Require a pull request before merging (0 required approvals is fine for a
     solo maintainer; Scorecard rewards ≥1, see below)
   - Require status checks to pass: `Lint and test`, both
     `Docker build and smoke test (...)` jobs, and `Analyze (python)` /
     `Analyze (actions)` from CodeQL. Pick them from the list after they have
     run once.
   - Optionally *Require signed commits* and *Require linear history*.
8. **Workflow permissions**: Settings → Actions → General → *Workflow
   permissions* = **Read repository contents** (the workflows request any write
   scope they need), and leave *Allow GitHub Actions to create and approve pull
   requests* unchecked.
9. **SonarQube Cloud**: sign in at <https://sonarcloud.io> with GitHub, import
   the repository into organization `alws34` with project key
   `alws34_homelab-takeout`, set *Analysis method* to GitHub Actions (turn
   *Automatic Analysis* off), create a token and add it as the repository secret
   `SONAR_TOKEN`. Until then the Sonar job skips its steps.
10. **OpenSSF Best Practices**: register the repo at <https://www.bestpractices.dev>
    and answer the **Passing** questionnaire, then the **Baseline level 1**
    one. Most answers point at files above: `README.md` (what it does, how to
    get it), `CONTRIBUTING.md` (how to contribute, tests required for new
    workers), `SECURITY.md` (private reporting, 7-day acknowledgement),
    `LICENSE`, CI + CodeQL + ruff (automated tests, static analysis, warnings),
    tags `vX.Y.Z` + generated release notes (unique versioning, release notes),
    HTTPS everywhere, signed releases. After it is accepted, Scorecard's
    *CII-Best-Practices* check picks it up automatically.
11. **Optional `SCORECARD_READ_TOKEN`**: a fine-grained PAT scoped to this repo
    with read-only *Administration* (plus read-only Contents/Metadata) lets the
    Branch-Protection check read rulesets. Add it as a repository secret; the
    workflow falls back to `GITHUB_TOKEN` without it.
12. **First release**: push a tag `v1.0.0` (or similar) on `main`. `release.yml`
    builds, signs and publishes it. Then follow `docs/VERIFYING_RELEASES.md`
    once to confirm the commands work end to end.

## Scorecard checks that stay low for a solo maintainer

- **Code-Review**: counts merged changes approved by someone other than the
  author. A single maintainer merging their own PRs scores 0 to low, even with
  every change going through a PR. It improves only with a second reviewer.
- **Contributors**: counts contributors from multiple organizations over recent
  commits. A one-person project scores low until outside contributors land work.
- **Branch-Protection**: will not reach 10 without required approving reviews
  (and code-owner review), which a solo maintainer cannot satisfy. Without
  `SCORECARD_READ_TOKEN` it may also be unable to read rulesets.
- **Fuzzing**: 0 for now. No fuzzing or property-based tests exist; adding
  [Hypothesis](https://hypothesis.readthedocs.io/) tests for the parsers would
  count.
- **Packaging**: expected to be inconclusive. There is deliberately no published
  package or image; users build from source.
- **Signed-Releases** and **CII-Best-Practices** stay low until the first signed
  release exists and the bestpractices.dev entry is accepted.
