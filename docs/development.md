# Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q     # starts a throwaway postgres:18 container
```

The tests load TeslaMate's real table definitions (`db/teslamate-schema.sql`), start the app so
it creates its own schema, and also cover the optional `scripts/provision-db.sh` role.

## Dev stack

`scripts/dev.sh` runs the UI and Grafana on their own test database, not on the TeslaMate
network, so it can't touch live data. It listens on localhost unless `dev.env` says otherwise
(see `dev.env.example`).

```bash
scripts/dev.sh up             # http://localhost:8089/expenses-dev/ and :8090/expenses-dev/grafana/
scripts/dev.sh test-browser   # Playwright in a container; screenshots in .dev/screenshots
scripts/dev.sh refresh        # re-copy TeslaMate data, keeping your test expenses
scripts/dev.sh reset          # wipe the test database
scripts/dev.sh demo up         # second stack of invented data, for screenshots (see below)
scripts/dev.sh sample-expenses  # add made-up expenses to try the app with; `clear` removes them
scripts/dev.sh schema         # regenerate db/teslamate-schema.sql after a TeslaMate upgrade
scripts/dev.sh setup          # in a new worktree: create .venv
scripts/dev.sh down           # stop the stack, keeping its data
scripts/dev.sh logs           # follow the app's log
scripts/dev.sh psql           # a psql shell on the test database
scripts/dev.sh onedrive-save  # save a OneDrive sign-in for the dev stack to reuse
```

Each git worktree gets its own stack and test database at `/expenses-dev/<worktree>/`, so
parallel branches never share running code. `scripts/dev.sh info` shows a checkout's path and
ports, `list` shows every running dev stack, and `destroy` removes one (its containers, test database, image and `.dev` folder). `.worktreeinclude`
copies your `dev.env` into new Claude Code worktrees.

OneDrive is on in the dev stack with a throwaway token key in `.dev/env`. To sign in there,
add the dev stack's callback address (shown on its OneDrive page) as a second redirect URI on
your app registration. A sign-in writes to your real OneDrive, so give each car a test folder.

The seed only reads from TeslaMate. It copies all of `cars` and `charging_processes`, and one
`positions` row per hour with latitude, longitude and elevation blanked. `up` warns if
TeslaMate's tables no longer match the committed schema. Grafana also lists TeslaMate's bundled
dashboards, which won't work there because the test database only has these three tables.

## Releasing

Pushing a `v*` tag runs the tests and publishes a multi-arch image to
`ghcr.io/mattew124/teslamate-expenses`. Pre-release tags such as `v0.2.0-rc1` don't move `:latest`.
The run summary shows the digest, so what you test is what you deploy:

```bash
git tag v0.2.0-rc1 && git push origin v0.2.0-rc1
scripts/dev.sh up --image ghcr.io/mattew124/teslamate-expenses@sha256:...
scripts/dev.sh test-browser
```

Then pin production to that digest. Schema changes apply when the new version starts.

## Documentation and screenshots

This site is built from `docs/` by [Zensical](https://zensical.org) and published to GitHub Pages
each time `main` changes. To preview it:

```bash
.venv/bin/pip install -r requirements-docs.txt
```

```bash
.venv/bin/zensical serve
```

A pull request that changes what a user or installer sees updates the pages it affects. A new or
visibly changed screen also needs a screenshot in the docs.

**Screenshots must never show real data, so they can't come from the dev stack**, which copies your
real cars and charging costs. They come from the demo stack instead, whose data is invented:

```bash
scripts/dev.sh demo up
```

```bash
scripts/dev.sh demo screenshots
```

`demo up` builds a separate stack (its own database, app and Grafana, at `<path>-demo/`). Each time it is
built, the empty `cars`, `positions` and `charging_processes` tables are created from a fresh schema-only
dump of your live TeslaMate, so the demo can't fall behind TeslaMate's schema; no row is read. The rows
come from `scripts/demo-seed.sql` (one made-up car, three years of odometer readings and charging
sessions) plus the sample expenses. `demo reset` rebuilds it, `demo destroy` removes it. Files land in
`.dev/demo-screenshots`; copy the ones you want into `docs/images/` and look at each first, as the app
shows your own addresses on some screens (`tests/browser/screenshots.py` swaps those for example ones).
`dev.sh screenshots` works only after `demo`, as above. A test fails if an image in `docs/` isn't under `docs/images/`.

Where a page needs a screenshot that hasn't been taken yet, add a marker on its own line, at the place
in the page it belongs: an HTML comment holding `screenshot:`, a short name, a dash and what it should
show, as in `<!-- screenshot: settings-page - The Settings page -->`. Markers don't appear on the
site. To list the screenshots still to take:

```bash
git grep -n '^<!-- screenshot:' -- docs
```

Version numbering and the candidate-to-stable steps are in
[RELEASING.md](https://github.com/mattew124/teslamate-expenses/blob/main/RELEASING.md).

### Keeping the docs in step with the code

Three things check this, from cheapest to most thorough:

- `tests/test_docs_drift.py` fails when the app reads a setting, or `scripts/dev.sh` offers a command, that no page mentions.
- The *Docs sync* workflow reads each pull request's code changes and, if a page has fallen behind, commits a Markdown-only fix to the branch. It needs a `CLAUDE_CODE_OAUTH_TOKEN` repository secret (from `claude setup-token`), and it skips drafts, forks and bots.
- `/docs-sync` in Claude Code sweeps the whole repo for drift on demand.
