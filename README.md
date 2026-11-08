# teslamate-expenses

Track what your Tesla costs to own, next to [TeslaMate](https://github.com/teslamate-org/teslamate).

- **Web UI** (Flask) to add/edit expenses: category, date, time, amount, odometer, vendor,
  notes and a link to the PDF invoice in your cloud storage. The odometer can be filled in from
  TeslaMate's own position data for the chosen date and time.
- **Postgres schema** `expenses` inside the TeslaMate database, so Grafana joins it to
  TeslaMate's charging costs and odometer with no second datasource.
- **Grafana dashboard** "Cost of Ownership": total cost of ownership, purchase vs running
  vs charging, cost per km and per month, monthly spend by category, cumulative cost, and
  a table of every expense with a clickable invoice link.

Starter categories: Initial purchase, Insurance, Registration, Tyres, Cabin air filter,
Air filter, Wheel alignment, Tyre rotation. Add more at `/expenses/categories`
(new ones count as running costs).

```
 browser ──► proxy + forward-auth ──► expenses (Flask/gunicorn) ──► TeslaMate Postgres
                                                                       ▲   schema `expenses`
                                     Grafana (TeslaMate datasource) ───┘   + public.* (read-only)
```

## Setup

The app has **no login of its own**. Only publish it behind a forward-auth gate, such as
Authentik or Authelia with Traefik. `docker-compose.traefik.yml` refuses to start without one.

1. **Provision the database.** This is idempotent, so re-run it on every upgrade. It creates the
   `expenses` schema and a role that can write expenses and read only `cars` and
   `positions(car_id, date, odometer)`, nothing else in the TeslaMate database.

   ```bash
   export EXPENSES_DB_PASS=$(openssl rand -hex 24)
   ./scripts/provision-db.sh          # DB_CONTAINER defaults to teslamate-database-1
   ```

2. **Configure.** `cp .env.example .env`, and set `DATABASE_PASS` to the same value.
3. **Run it.** Pin a released image with `EXPENSES_IMAGE`, or leave it unset to build locally.

   ```bash
   docker compose up -d                                                     # localhost:8088/expenses/
   docker compose -f docker-compose.yml -f docker-compose.traefik.yml up -d # behind Traefik and forward-auth
   ```

   It joins TeslaMate's compose network (`TESLAMATE_NETWORK`, default `teslamate_default`).
4. **Grafana.** Dashboards → New → Import → upload `grafana/cost-of-ownership.json`
   (datasource `TeslaMate`, tested on Grafana 13). Set the **Timezone** box to your IANA zone and
   save the dashboard, so months line up with your local calendar.

## What the dashboard counts

All of it lives in the `expenses.ownership` view (`db/schema.sql`), one row per car:

| Figure | Definition |
|---|---|
| Total cost | purchase + running expenses + `charging_processes.cost` |
| Distance | latest odometer − odometer on the purchase expense (else the first odometer TeslaMate saw) |
| Cost per km — all-in | total cost ÷ distance |
| Cost per km — running | (running + charging) ÷ distance, excludes the purchase price |

Headline panels are all-time; the charts, pie and table follow the dashboard time range.
Currency is shown as `$` and distance in km.

## Notes

- **Invoice links** must be `https://` (rendered as a link, so `javascript:`/`data:` are
  refused). Whoever opens one needs access to it, and it lasts as long as its sharing settings.
- **Timezone.** Date and time are entered in `APP_TZ` and stored as `timestamptz`.
  TeslaMate's `positions.date` is naive UTC; the odometer lookup converts before comparing.
- POSTs must carry a same-origin `Origin`/`Referer` header (CSRF guard). Plain `curl` needs
  `-H 'Origin: https://your-host'`.
- Not covered yet: depreciation or resale value (so "TCO" is spend, not net cost), uploading
  invoices to the app itself, deleting categories.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q     # starts a throwaway postgres:18 container
```

The tests load TeslaMate's real table definitions (`db/teslamate-schema.sql`) and run the real
`scripts/provision-db.sh`.

### Dev stack

`scripts/dev.sh` runs the UI and Grafana on their own test database, not on the TeslaMate
network, so it can't touch live data. It listens on localhost unless `dev.env` says otherwise
(see `dev.env.example`).

```bash
scripts/dev.sh up             # http://localhost:8089/expenses-dev/ and :8090/expenses-dev/grafana/
scripts/dev.sh test-browser   # Playwright in a container; screenshots in .dev/screenshots
scripts/dev.sh refresh        # re-copy TeslaMate data, keeping your test expenses
scripts/dev.sh reset          # wipe the test database
scripts/dev.sh schema         # regenerate db/teslamate-schema.sql after a TeslaMate upgrade
scripts/dev.sh setup          # in a new worktree: create .venv
```

Each git worktree gets its own stack and test database at `/expenses-dev/<worktree>/`, so
parallel branches never share running code. `scripts/dev.sh info` shows a checkout's path and
ports, `list` shows every running dev stack, and `destroy` removes one. `.worktreeinclude`
copies your `dev.env` into new Claude Code worktrees.

The seed only reads from TeslaMate. It copies all of `cars` and `charging_processes`, and one
`positions` row per hour with latitude, longitude and elevation blanked. `up` warns if
TeslaMate's tables no longer match the committed schema. Grafana also lists TeslaMate's bundled
dashboards, which won't work there because the test database only has these three tables.

### Releasing

Pushing a `v*` tag runs the tests and publishes a multi-arch image to
`ghcr.io/mattew124/teslamate-expenses`. Pre-release tags such as `v0.2.0-rc1` don't move `:latest`.
The run summary shows the digest, so what you test is what you deploy:

```bash
git tag v0.2.0-rc1 && git push origin v0.2.0-rc1
scripts/dev.sh up --image ghcr.io/mattew124/teslamate-expenses@sha256:...
scripts/dev.sh test-browser
```

Then pin production to that digest. Schema changes reach an existing install by re-running
`scripts/provision-db.sh`.

## License

MIT. `db/teslamate-schema.sql` is generated from [TeslaMate](https://github.com/teslamate-org/teslamate)'s
table definitions (AGPL-3.0).
