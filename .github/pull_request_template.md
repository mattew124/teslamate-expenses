## What and why

<!-- What changes, and why. Link the issue if there is one. The pull request title becomes a line in the release notes, so make it clear. -->

## Checklist

- [ ] One feature or fix, and a failing test written first
- [ ] `.venv/bin/python -m pytest -q` passes
- [ ] `scripts/dev.sh test-browser` passes, and I looked at the screenshots (UI or dashboard changes)
- [ ] OneDrive changes: checked against a real account with `scripts/dev.sh test-browser --onedrive`, using a test folder
- [ ] Linter findings fixed, or excused on one line with the reason (no project-wide ignores)
- [ ] Added to **Unreleased** in `CHANGELOG.md`
- [ ] Schema change: `tmexpenses/schema.sql` stays idempotent, `db/grants.sql` and `scripts/provision-db.sh` still work with `SKIP_MIGRATE=1`
- [ ] Nothing installation-specific (hostnames, IPs, personal data, real invoices) in code, docs, logs or screenshots

## Documentation

- [ ] The pages in `docs/` that this changes are updated, or nothing a user or installer sees has changed.
- [ ] A new or visibly changed screen has a screenshot marker where it belongs in `docs/`, or there is no such screen. See "Documentation and screenshots" in `docs/development.md`.
- [ ] No screenshot, example or log in this change shows real data.

## Upgrade impact

<!-- Delete what doesn't apply. -->

- [ ] Nothing: pull the new image
- [ ] Installers on the restricted database role must re-run `scripts/provision-db.sh` first
- [ ] Breaking: a removed or renamed variable, a changed URL, or a changed dashboard UID (describe below)
