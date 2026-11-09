# Changelog

All notable changes to teslamate-expenses. Version numbers and the candidate-to-stable
steps are in [RELEASING.md](RELEASING.md). Newest first.

Each entry says what an installer must do to upgrade. Where nothing is said, pull the new image.

## Unreleased

### Added

- Add and Edit expense: **Choose from OneDrive**, a button under the PDF field, opens a window over the form to pick a PDF that is
  already in OneDrive. It starts in the car's folder and can go up, to the top of the drive. The file's
  link becomes the invoice link and the form fills in from it, as for a chosen file. Nothing is
  copied. The button shows OneDrive's logo, fetched once from `cdn.jsdelivr.net` and kept in memory rather
  than shipped with the app; `PROVIDER_ICONS=0` stops that (a plain cloud is shown). Needs nothing to upgrade.

## 0.2.0-rc2 - 2026-11-10

The second candidate for 0.2.0. Once it has been tested on production, the same commit is
tagged `v0.2.0`, the first stable release. Everything under 0.2.0-rc1 below applies to
anyone upgrading to it, including its action-needed items.

### Added

- A documentation site (built from `docs/`, published to GitHub Pages) covering setup, OneDrive,
  importing, the dashboard and reference. The README is now a short overview that links to it.
- Every page's footer shows the installed version and links to the documentation. A released
  image also asks `api.github.com`, in the background and at most every six hours, whether a
  newer release exists, and links to its release notes. Local builds never ask. Set
  `UPDATE_CHECK=0` to switch the check off.
- The release workflow creates a GitHub Release for each tag, with notes generated from the
  merged pull requests. Candidates are marked pre-releases.

### Changed

- Settings: the invoice date format and file name fields now hold their defaults as real,
  editable text instead of a placeholder that vanishes as you type. Saving the default
  unchanged stores "default", so it keeps following the default.
- The OneDrive page's note about file names now shows the name your current Settings
  produce, and links to Settings. It used to show a fixed example that didn't match.
- OneDrive: the page says that a newly added redirect URI can take a few minutes to work.

### Documentation

- `CONTRIBUTING.md`, plus pull request and issue templates with checklists for contributors.
- README setup follows a real deployment: back up first, set `FQDN_TM` before starting,
  check the app started, and recreate Grafana.
- README: the app creates its own tables when OneDrive is turned on, so there is no
  separate provisioning step.

### Upgrading

No schema change since 0.2.0-rc1. One new optional variable, `UPDATE_CHECK` (default on; `0`
switches off the release check, the only time the app contacts GitHub). Anyone coming from
earlier than rc1 should read its action-needed items below.

## 0.2.0-rc1 - 2026-11-09

### Action needed before upgrading

- **`ONEDRIVE_TOKEN_KEY` is now `EXPENSES_ENCRYPTION_KEY`.** Rename it in your environment.
  The old name still works but logs a warning pointing at the new one. Keep this key out
  of database backups: it is what protects the stored OneDrive sign-in.
- **Restricted database role** (`SKIP_MIGRATE=1`): re-run `scripts/provision-db.sh` first.
  This release adds two tables and a column, which the app won't create itself in that mode.

### Added

- Import existing invoices from OneDrive, reading the PDFs for vendor, date, amount and
  odometer. The import page opens at once and shows progress while it reads.
- The importer learns labels and vendors from your corrections (labels and categories
  only, never amounts or document text).
- Odometer from TeslaMate on import, and an optional rename of the OneDrive file.
- The Add page prefills from the invoice PDF, and the PDF field now comes first.
- Uploads go into per-category subfolders of the car's OneDrive folder, editable on the
  Categories page.
- Settings: an invoice file name template with `%date %category %vendor %car %amount`
  aliases.
- Settings: which figure to take when an invoice shows several amounts (6 or 12 months):
  the largest (default), the smallest, or leave blank.
- A warning about likely duplicates before an expense is saved.
- `LOG_LEVEL` (debug, info, warning, error) for the app and gunicorn. File names, which
  can hold a vendor, are logged at debug only.
- The app writes its Grafana dashboard to a shared volume for Grafana to provision.
- A site icon, and a README header with badges.
- Pre-release tags also publish the floating `:rc` image tag.

### Changed

- The vendor is the trading name, not the legal entity.
- The PDF's own contents are trusted over its file name.
- The import page calls its template flag "scanning", which is what it means.
- Developer tooling: a private file outside the repository holds the dev OneDrive sign-in,
  and worktrees run real OneDrive checks against a test folder only.

### Fixed

- A crash on PDFs over 500 KB, and more real-world invoice layouts are read correctly.
- The header's icon sits beside the Expenses link, which goes home.

### Security and quality

- CodeQL, Trivy, OpenSSF Scorecard, Dependabot, Ruff, Bandit, Hadolint and coverage in CI,
  on Python 3.15. Reviewed lint exceptions are excused where they occur, with the reason.
- GitHub Actions dependencies updated.

## 0.1.0-rc1 - 2026-11-08

The first candidate.

### Added

- Expense tracking and total cost of ownership for TeslaMate: a Flask UI, an `expenses`
  schema in the TeslaMate database, and a Grafana dashboard.
- The app connects as TeslaMate's own database user and creates its `expenses` schema on
  start. `scripts/provision-db.sh` remains as the optional least-privilege path.
- Upload invoice PDFs to OneDrive, into a folder per car. Sign in on Microsoft's page,
  choose folders by browsing, and create a new folder from the browser. Settings include
  the date format in uploaded file names (default `yymmdd`).
- Links to the UI and Grafana from the URLs set in TeslaMate's Settings, kept on the app's
  own Settings page and pre-filled from `FQDN_TM` on first start.
- A step-by-step Microsoft app registration walkthrough in the README and on the OneDrive
  page.
- A separate dev stack per git worktree, for parallel sessions.
- Release process in RELEASING.md.

### Fixed

- The header layout on phones.
- OneDrive Connect now goes to Microsoft by navigation, not a form redirect.

`0.1.0-rc1` was superseded by `0.2.0-rc1` and never released as stable. The version number
jumped because 0.2.0-rc1 added tables and renamed an environment variable.
