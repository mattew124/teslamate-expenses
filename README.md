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
4. **OneDrive (optional).** See [OneDrive uploads](#onedrive-uploads).
5. **Grafana.** Dashboards → New → Import → upload `grafana/cost-of-ownership.json`
   (datasource `TeslaMate`, tested on Grafana 13). Set the **Timezone** box to your IANA zone and
   save the dashboard, so months line up with your local calendar.

## OneDrive uploads

With this on, the expense form takes the invoice PDF itself. The app uploads it to the folder
you chose for that car, names it `2026-11-07 Tyres - Tyre Shop.pdf` (date, category, vendor;
an existing name gets a suffix, never overwritten) and stores the file's OneDrive link as the
expense's invoice link. It works with a personal Microsoft account (OneDrive Personal).

1. **Turn it on.** Add a key to `.env`, re-run `scripts/provision-db.sh` (it adds two tables)
   and restart the app:

   ```bash
   ONEDRIVE_TOKEN_KEY=<output of: openssl rand -base64 32 | tr '+/' '-_'>
   ```

   The key encrypts the client secret and the saved sign-in (a refresh token for your whole
   OneDrive) before they go into the database, so a database dump or backup on its own can't
   open the drive. Keep the key out of those backups. If you lose or change it, enter the
   secret and connect again.
2. **Open the OneDrive page** in the app and follow its steps. It shows the exact redirect URI
   to register:
   - In [Microsoft Entra → App registrations](https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade),
     register an app for **Personal Microsoft accounts only**, with a **Web** redirect URI of
     `https://<your host><BASE_PATH>/onedrive/callback`.
   - Add a client secret, and paste the client ID and the secret's value into the page.
     Secrets last at most 24 months; enter the expiry date and the page warns you 30 days
     before it runs out.
3. **Connect OneDrive.** You sign in on Microsoft's page and come straight back. The app asks
   for delegated `Files.ReadWrite` and `offline_access`.
4. **Choose a folder for each car** by browsing your OneDrive. Uploads go by the folder's ID,
   so renaming or moving it later doesn't break them.

Every installation registers its own app because Microsoft only redirects to addresses
registered on the app, and each installation has its own. Sign-in uses PKCE, and the
callback must return to the browser that started it.

The redirect URI is built from the proxy's `X-Forwarded-Proto` and `X-Forwarded-Host`
headers, so the proxy must set them (Traefik does).

PDFs only (checked by content, not just the name), up to 20 MB. Uploading on the edit page
replaces the link; the old file stays in OneDrive. Disconnecting deletes the saved sign-in;
to revoke the app's access entirely, remove it at
[account.live.com/consent/Manage](https://account.live.com/consent/Manage).

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

OneDrive is on in the dev stack with a throwaway token key in `.dev/env`. To sign in there,
add the dev stack's callback address (shown on its OneDrive page) as a second redirect URI on
your app registration. A sign-in writes to your real OneDrive, so give each car a test folder.

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
