# teslamate-expenses

Track what your Tesla costs to own, next to [TeslaMate](https://github.com/teslamate-org/teslamate).

- **Web UI** (Flask) to add/edit expenses: category, date, time, amount, odometer, vendor,
  notes and a link to the PDF invoice in your cloud storage. The odometer can be filled in from
  TeslaMate's own position data for the chosen date and time.
- **Optional OneDrive upload**: choose the invoice PDF in the form and it goes into that car's
  OneDrive folder, with the link filled in for you.
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

1. **Configure.** `cp .env.example .env` and set `DATABASE_USER` and `DATABASE_PASS` to the same
   values TeslaMate and its other add-ons use (they're in TeslaMate's compose file).
2. **Run it.** Pin a released image with `EXPENSES_IMAGE`, or leave it unset to build locally.

   ```bash
   docker compose up -d                                                     # localhost:8088/expenses/
   docker compose -f docker-compose.yml -f docker-compose.traefik.yml up -d # behind Traefik and forward-auth
   ```

   It joins TeslaMate's compose network (`TESLAMATE_NETWORK`, default `teslamate_default`).
3. **OneDrive (optional).** See [OneDrive uploads](#onedrive-uploads).
4. **Grafana.** Dashboards → New → Import → upload `grafana/cost-of-ownership.json`
   (datasource `TeslaMate`, tested on Grafana 13). Set the **Timezone** box to your IANA zone and
   save the dashboard, so months line up with your local calendar.
5. **Link the two.** Set `FQDN_TM` in `.env` to the same value as TeslaMate's compose variable. On
   first start the app uses it to pre-fill its **Settings** page (the same idea as TeslaMate's): this
   app at `https://$FQDN_TM$BASE_PATH/` and the dashboard at `https://$FQDN_TM/grafana/d/tm-expenses-tco`
   (change the `/grafana` part with `GRAFANA_PATH`). Those addresses add a *Dashboards ↗* link to the
   app's header and make the dashboard's *Add / edit expenses* link go to the right place. After
   that, the Settings page is the only place to change them, and restarts never overwrite it.
   Without `FQDN_TM` they start empty, and you fill them in there.

### The database

There is nothing to run. On start the app creates its own `expenses` schema in the TeslaMate
database (idempotent, so upgrades apply themselves), and only ever writes inside it. It reads
`cars`, `positions` and `charging_processes`. This needs TeslaMate's database user, which
owns the database; Grafana already reads it the same way.

To give the app less than that, run `scripts/provision-db.sh` once. It creates a role that
can write expenses and read only `cars` and `positions(car_id, date, odometer)`. Point
`DATABASE_USER`/`DATABASE_PASS` at it and set `SKIP_MIGRATE=1`. Re-run the script on upgrades.

## OneDrive uploads

With this on, the expense form takes the invoice PDF itself. The app uploads it to the folder
you set for that car, names it `2026-11-07 Tyres - Tyre Shop.pdf` (date, category, vendor;
an existing name gets a suffix, never overwritten) and stores the file's OneDrive link as the
expense's invoice link. It works with a personal Microsoft account (OneDrive Personal).

1. **Register an app** at [Microsoft Entra → App registrations](https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade)
   → New registration:
   - Supported account types: **Personal Microsoft accounts only** (or any org directory and
     personal accounts).
   - No redirect URI.
   - Then, under Authentication, set **Allow public client flows** to **Yes**.
   - Copy the **Application (client) ID**. There is no client secret.

   The app signs in with the device code flow, so it asks for delegated `Files.ReadWrite` and
   `offline_access` and needs no redirect URI on your hostname.
2. **Configure.** In `.env`:

   ```bash
   ONEDRIVE_CLIENT_ID=<application (client) id>
   ONEDRIVE_TOKEN_KEY=<output of: openssl rand -base64 32 | tr '+/' '-_'>
   ```

   The key encrypts the saved sign-in (a refresh token for your whole OneDrive) before it goes
   into the database, so a database dump or backup on its own can't open the drive. Keep the key
   out of those backups. If you lose or change it, connect again.
3. **Re-run `scripts/provision-db.sh`** (it adds the two tables), restart the app, open the
   **OneDrive** page, press **Connect OneDrive**, enter the code shown at
   microsoft.com/devicelogin, then press **I've signed in**.
4. **Set a folder per car** on the same page, e.g. `Cars/Model 3/Invoices`, from the top of
   your OneDrive. Missing folders are created on the first upload.

PDFs only (checked by content, not just the name), up to 20 MB. Uploading on the edit page
replaces the link; the old file stays in OneDrive. Disconnecting deletes the saved sign-in;
to revoke the app's access entirely, remove it at
[account.live.com/consent/Manage](https://account.live.com/consent/Manage).

## What the dashboard counts

All of it lives in the `expenses.ownership` view (`tmexpenses/schema.sql`), one row per car:

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
  An uploaded invoice's link is its plain OneDrive address, not a share link: it opens for you,
  signed in to that Microsoft account, and for nobody else.
- **Timezone.** Date and time are entered in `APP_TZ` and stored as `timestamptz`.
  TeslaMate's `positions.date` is naive UTC; the odometer lookup converts before comparing.
- POSTs must carry a same-origin `Origin`/`Referer` header (CSRF guard). Plain `curl` needs
  `-H 'Origin: https://your-host'`.
- Not covered yet: depreciation or resale value (so "TCO" is spend, not net cost), storing
  invoices in the app itself (they go to your cloud storage), deleting categories.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q     # starts a throwaway postgres:18 container
```

The tests load TeslaMate's real table definitions (`db/teslamate-schema.sql`), start the app so
it creates its own schema, and also cover the optional `scripts/provision-db.sh` role.

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

To try OneDrive uploads in dev, set `ONEDRIVE_CLIENT_ID` in `dev.env`; the token key is
generated into `.dev/env`. A sign-in there writes to your real OneDrive, so give each car a test
folder.

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

Then pin production to that digest. Schema changes apply when the new version starts.

## License

MIT. `db/teslamate-schema.sql` is generated from [TeslaMate](https://github.com/teslamate-org/teslamate)'s
table definitions (AGPL-3.0).
