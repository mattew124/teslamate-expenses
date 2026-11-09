## Summary

<!-- What changes, and why. Link the issue if there is one. -->

## Checklist

- [ ] One feature or fix, and a failing test written first
- [ ] `.venv/bin/python -m pytest -q` passes
- [ ] `scripts/dev.sh test-browser` passes, and I looked at the screenshots (UI or dashboard changes)
- [ ] OneDrive changes: checked against a real account with `scripts/dev.sh test-browser --onedrive`, using a test folder
- [ ] Linter findings fixed, or excused on one line with the reason (no project-wide ignores)
- [ ] Added to **Unreleased** in `CHANGELOG.md`
- [ ] Schema change: `tmexpenses/schema.sql` stays idempotent, `db/grants.sql` and `scripts/provision-db.sh` still work with `SKIP_MIGRATE=1`
- [ ] Nothing installation-specific (hostnames, IPs, personal data, real invoices) in code, docs, logs or screenshots

## Upgrade impact

<!-- Delete what doesn't apply. -->

- [ ] Nothing: pull the new image
- [ ] Installers on the restricted database role must re-run `scripts/provision-db.sh` first
- [ ] Breaking: a removed or renamed variable, a changed URL, or a changed dashboard UID (describe below)
