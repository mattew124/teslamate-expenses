# Releasing

Releases are tags on `main`. There is no release branch: `main` is always releasable
(CI must pass before anything merges), and the owner decides when to tag it.

## Version numbers: `MAJOR.MINOR.PATCH`

Ask "what must an installer do to upgrade?"

| Bump | When | Upgrade means |
|---|---|---|
| **PATCH** `0.2.0` → `0.2.1` | A bug fix, a wording or layout fix, a dependency update, docs. No new setting, no schema change. | Pull the new image. |
| **MINOR** `0.2.1` → `0.3.0` | A new feature or setting, or an **additive** schema change (a new table or column, created idempotently on start). Old configuration keeps working. | Pull the new image. Anyone using the restricted database role (`SKIP_MIGRATE=1`) re-runs `scripts/provision-db.sh` first, because the app won't create the new objects itself. |
| **MAJOR** `0.9.0` → `1.0.0` | A breaking change: a removed or renamed environment variable, a new required one, a schema change that isn't additive or that moves data, a changed URL or `BASE_PATH` behaviour, new database grants beyond what the script gives, or a changed dashboard UID. | Read the release notes and act before upgrading. |

Rules of thumb:

- If a release needs an action from installers, say so in its release notes, whatever the bump.
- When a change is both a feature and a fix, take the larger bump.
- Never reuse or move a published tag. A bad release gets a new PATCH.
- **Before 1.0.0** the project is still settling: a breaking change bumps MINOR instead of MAJOR.
  Release `1.0.0` once it has run in production for a while and the setup and schema have stopped
  moving.

## Candidates and stable releases

1. Merge the work to `main` (pull request, CI green).
2. Tag a **release candidate**: `git tag -a v0.3.0-rc1 -m "v0.3.0-rc1" && git push origin v0.3.0-rc1`.
   The Release workflow runs the tests, builds `ghcr.io/<owner>/teslamate-expenses:0.3.0-rc1` for
   amd64 and arm64, and prints the digest in the run summary. A pre-release tag never moves `:latest`.
3. Test that digest from a worktree: `scripts/dev.sh up --image <ref>@<digest>`, `scripts/dev.sh onedrive-link`,
   then `scripts/dev.sh test-browser --onedrive` (it fails if the real OneDrive checks can't run), then
   look at the pages yourself.
4. Fix anything on `main` and tag `-rc2`, `-rc3`, and so on.
5. When a candidate passes, tag the **same commit** `v0.3.0`. This publishes `0.3.0` and moves `:latest`.
6. Pin production to the digest of the stable build (the run summary shows it), not to `:latest`.
   The stable tag is a fresh build, so its digest differs from the candidate's: give it the same
   quick check before pinning.

## Following a channel

| Tag | Moves on | For |
|---|---|---|
| `X.Y.Z-rcN` | never (fixed) | Testing one exact candidate; pin by digest. |
| `rc` | every `-rc` tag | An installation that follows the candidate channel. |
| `latest` | every stable `vX.Y.Z` tag | An installation that follows stable. |
| `X.Y.Z` | never (fixed) | Pinning a stable release. |

A floating tag only changes what a pull fetches. Whether the running container updates is up
to the installation's own update process. A release that adds tables or columns needs
`scripts/provision-db.sh` re-run first on installations using the restricted role.

## Release notes

The same workflow ends by creating a GitHub Release for the tag, with notes generated from the merged
pull requests since the previous tag. A tag with a hyphen (`-rc1`) is marked a pre-release and never
becomes "latest". These are the notes the app's footer links to, and the releases it compares with to
say whether an installation is up to date. Write a clear pull request title: it is the line in the notes.
Edit the release on GitHub afterwards if it needs more, such as an upgrade step.

## How the image is built

No Makefile. The `Dockerfile` in the repository root is the whole recipe, and
`.github/workflows/release.yml` runs on any `v*` tag: tests first (`ci.yml`), then
`docker/build-push-action` pushes to ghcr.io using the workflow's own `GITHUB_TOKEN`
(no stored secrets), and passes the tag as the `VERSION` build argument, which the image keeps in
`APP_VERSION` (shown in the app's footer). Pushes to `main` and pull requests run only the tests.
