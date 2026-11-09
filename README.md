<p align="center"><img src="tmexpenses/static/icon.svg" alt="" width="96" height="96"></p>

<h1 align="center">teslamate-expenses</h1>

<p align="center">Track what your Tesla costs to own, next to <a href="https://github.com/teslamate-org/teslamate">TeslaMate</a>.</p>

<p align="center">
  <a href="https://github.com/mattew124/teslamate-expenses/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/mattew124/teslamate-expenses/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/mattew124/teslamate-expenses/releases"><img alt="Latest release" src="https://img.shields.io/github/v/release/mattew124/teslamate-expenses?include_prereleases&sort=semver"></a>
  <a href="https://github.com/mattew124/teslamate-expenses/pkgs/container/teslamate-expenses"><img alt="Container image" src="https://img.shields.io/badge/ghcr.io-teslamate--expenses-blue?logo=docker&logoColor=white"></a>
  <a href="LICENSE"><img alt="MIT licence" src="https://img.shields.io/github/license/mattew124/teslamate-expenses"></a>
  <img alt="Vibe coded" src="https://img.shields.io/badge/vibe-coded-ff69b4">
</p>

> **Vibe coded.** This project was written with an AI coding assistant (Claude Code), directed
> and tested by a person, not hand-written or audited line by line. It has tests, runs in a
> read-only, non-root container and keeps its database role least-privilege, but treat it as you
> would any young, unreviewed software: back up your TeslaMate database before first use, and
> read the code before you trust it with anything you care about.
>
> Not affiliated with TeslaMate or Tesla. It's an unofficial add-on that reads TeslaMate's data.

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
4. **Grafana.** The app writes its own dashboard, with your `APP_TZ` as the default timezone, to
   the `teslamate-expenses-dashboards` volume each time it starts, so the dashboard always matches
   the version you run. Give TeslaMate's Grafana that volume and `grafana/provider.yml`, in
   TeslaMate's compose file:

   ```yaml
   services:
     grafana:
       volumes:
         - teslamate-expenses-dashboards:/dashboards-expenses:ro
         - /path/to/teslamate-expenses/grafana/provider.yml:/etc/grafana/provisioning/dashboards/expenses.yml:ro
   volumes:
     teslamate-expenses-dashboards:
       external: true      # created when you start this app's compose file
   ```

   Restart Grafana once. The dashboard appears under **Expenses** (datasource `TeslaMate`, tested
   on Grafana 13). Provisioned dashboards can't be edited in Grafana; to customise, import
   `grafana/cost-of-ownership.json` by hand instead (Dashboards → New → Import), set the
   **Timezone** box to your IANA zone and save, and set `DASHBOARD_DIR=` empty in `.env`.
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

With this on, the expense form takes the invoice PDF itself. The app uploads it to a subfolder of
the folder you chose for that car (made when first needed, and found again whatever its case),
names it `261107 Tyres - Tyre Shop.pdf` (date, category, vendor;
an existing name gets a suffix, never overwritten) and stores the file's OneDrive link as the
expense's invoice link. It works with a personal Microsoft account (OneDrive Personal).
The date part is `yymmdd` unless you change it under **Settings → Invoice file name date**
(for example `yyyy-mm-dd`). The whole name is a template under **Settings → Invoice file name**,
a single line of `%` aliases (default `%date %category - %vendor`):

| Alias | Becomes |
|---|---|
| `%date` | the invoice date, in the date format above |
| `%category` | the category, e.g. Tyres |
| `%vendor` | the vendor or shop (left out when blank) |
| `%car` | the car's name |
| `%amount` | the amount paid, e.g. 1234.50 |
| `%%` | a percent sign |

For example `%date - %category - %vendor` gives `261107 - Tyres - Tyre Shop.pdf`. It must include
`%date` and `%category` or `%vendor`. A blank alias is left out together with the separator before it
(no vendor gives `261107 - Tyres.pdf`). It applies to new uploads and to renames on import; files
already in OneDrive keep their names.

The subfolder is set per category on the **Categories** page. The standard categories start
mapped (Tyres, filters, alignment and rotation to `Maintenance`; Insurance and Registration to
folders of the same name); a blank box means a folder named after the category.

1. **Turn it on.** Add a key to `.env`, re-run `scripts/provision-db.sh` (it adds two tables)
   and restart the app:

   ```bash
   EXPENSES_ENCRYPTION_KEY=<output of: openssl rand -base64 32 | tr '+/' '-_'>
   ```

   The key encrypts the client secret and the saved sign-in (a refresh token for your whole
   OneDrive) before they go into the database, so a database dump or backup on its own can't
   open the drive. Keep the key out of those backups. If you lose or change it, enter the
   secret and connect again.
2. **Register an app with Microsoft** (once per installation; see
   [Registering the app](#registering-the-app) below), then enter its client ID and secret on
   the app's **OneDrive** page. The page shows the exact redirect URI to register.
3. **Connect OneDrive.** You sign in on Microsoft's page and come straight back. The app asks
   for delegated `Files.ReadWrite` and `offline_access`.
4. **Choose a folder for each car** by browsing your OneDrive, creating one with **New folder**
   if it doesn't exist yet. Uploads go by the folder's ID, so renaming or moving it later
   doesn't break them.

### Registering the app

Any Microsoft account can register the app, including the personal one that owns the OneDrive.
It needs no paid Azure subscription: Microsoft creates a free directory for personal-account
registrations. The account choice that matters comes later, at **Connect OneDrive**, where you
sign in as the account whose OneDrive gets the invoices.

1. In the [Microsoft Entra admin center](https://entra.microsoft.com), go to **Identity** →
   **Applications** → **App registrations** → **New registration**.
2. **Name:** anything, e.g. `teslamate-expenses`.
3. **Supported account types:** **Personal Microsoft accounts only**. "Accounts in any
   organizational directory and personal Microsoft accounts" also works.
4. **Redirect URI:** platform **Web**, and the address shown on the app's OneDrive page,
   `https://<your host><BASE_PATH>/onedrive/callback`. It must match byte for byte, scheme
   and path included, and Microsoft accepts plain `http://` only for `localhost`. Then
   **Register**.
5. **Authentication:** leave **Allow public client flows** at **No** (the default). Turning it on
   makes Microsoft treat the app as a public client, and every token refresh then fails with
   `AADSTS90023: Public clients can't send a client secret`.
6. **API permissions** (optional): **Add a permission** → **Microsoft Graph** →
   **Delegated permissions** → `Files.ReadWrite` and `offline_access`. If you skip this, you're
   asked to consent to the same two at the first sign-in.
7. **Certificates & secrets** → **Client secrets** → **New client secret**. Copy its **Value**
   straight away; it's shown only once, and the **Secret ID** next to it is not the secret.
   Secrets last at most 24 months, so note the **Expires** date.
8. From **Overview**, copy the **Application (client) ID**. It isn't secret.
9. On the app's **OneDrive** page, paste the client ID, the secret's value and its expiry date,
   then press **Connect OneDrive**.

A dev stack or second installation needs its own callback address: add it as another **Web**
redirect URI on the same registration.

Why the normal web sign-in and not a device code or the "nativeclient" redirect: both have
been tried with personal accounts and fail. A device-code sign-in marks the app as a public
client and breaks refreshes (`AADSTS90023` above). Microsoft's out-of-band
`https://login.microsoftonline.com/common/oauth2/nativeclient` page now redirects to an error
page after a few seconds, which made rclone and the
[abraunegg/onedrive](https://github.com/abraunegg/onedrive/discussions/3558) client drop it too.

References: [Register an application](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app) ·
[Authorization code flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow) ·
[Graph permissions](https://learn.microsoft.com/en-us/graph/permissions-reference) ·
[Remove an app's access to your account](https://account.live.com/consent/Manage)

Every installation registers its own app because Microsoft only redirects to addresses
registered on the app, and each installation has its own. Sign-in uses PKCE, and the
callback must return to the browser that started it.

The redirect URI is built from the proxy's `X-Forwarded-Proto` and `X-Forwarded-Host`
headers, so the proxy must set them (Traefik does).

PDFs only (checked by content, not just the name), up to 20 MB. Uploading on the edit page
replaces the link; the old file stays in OneDrive. Disconnecting deletes the saved sign-in;
to revoke the app's access entirely, remove it at
[account.live.com/consent/Manage](https://account.live.com/consent/Manage).

### Importing invoices you already have

Invoices already in a car's OneDrive folder (and its subfolders) can be turned into expenses:
**OneDrive → Import existing invoices** beside the car. The page opens at once and shows its progress (looking in the folder, then "Reading 3 of 10" with
the file's name) while it reads up to 10 PDFs at a time that aren't yet linked to an expense, guesses each one's date, category, vendor, amount and odometer, and shows
them in a table to check, correct and untick. **Nothing is saved until you press Import**, and a
file already linked to an expense never appears again, so you can work through a big folder in
rounds.

The guesses come from the file name (a leading `yymmdd`, `yyyymmdd` or `yyyy-mm-dd`, and a
category name in it or in its folder name, as in `Tyres/261024 - Tyres - Tyre Shop.pdf`), then
the PDF's text (`Invoice Date`, a `Total` line, `Odometer`). Scanned PDFs with no text are read
by OCR, which is slower and rougher. When a `Total due` line offers several amounts (a registration renewal for 6 or 12 months), **Settings →
When an invoice offers several amounts** picks the largest (the default), the smallest, or leaves it blank for
you. An amount is never guessed without a clear total, and
rows missing a date, category or amount start unticked. The image includes `poppler-utils` and
`tesseract-ocr` for this; without them the app still runs and simply proposes less.

A blank odometer is filled from TeslaMate's last position before the invoice date. Each row can also
be **renamed in OneDrive** to the file name template from **Settings → Invoice file name**, built from the
values you confirm; a name already taken leaves the file as it is, and the expense is still imported.
On **+ Add**, choosing the invoice PDF
fills in the date, amount, vendor, category (when the file name or a learned vendor says) and
odometer it can read, without touching anything you've already typed.

**Possible duplicates** are flagged. Saving a new expense (the Add form) that has the same car and exactly
the same amount as one within 14 days either side asks first: it lists the match with a link to open it, and
**Save anyway** saves the new one regardless. The question comes before anything is uploaded to OneDrive, and the
server asks again if the page's own check was skipped. The import preview does the same per file, leaving a
likely duplicate unticked with a link to the expense it resembles.

The importer **learns from your corrections**. When you change or fill in a date, amount or
odometer, it re-reads that PDF and finds your value in the text: the label printed beside it
(for example `Annual premium` or `Date of issue`) is remembered, never the value itself. It also
remembers which category you gave each vendor's invoices. A label or vendor is used for later
guesses once you've confirmed it twice; a learned label that finds two different values in one
PDF leaves the field blank rather than guessing. Nothing is learned if your value isn't in the
PDF text. **Import page → See and remove what it has learned** lists everything and lets you
delete it. This stores only short labels and vendor names, in the `expenses` schema.

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
- **Logs.** `docker logs` shows gunicorn's request lines plus the app's own events. `LOG_LEVEL`
  (`debug`, `info` the default, `warning`, `error`; set in `.env`, restart to change) sets both.
  `info` records startup, schema changes, expenses saved, updated or deleted, and OneDrive
  sign-in and upload results. `debug` adds detail such as retries. Tokens, secrets, vendors,
  notes and amounts are never logged, except that file names, which can contain a vendor,
  appear at `debug`.
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

Then pin production to that digest. Schema changes apply when the new version starts.

## License

MIT. `db/teslamate-schema.sql` is generated from [TeslaMate](https://github.com/teslamate-org/teslamate)'s
table definitions (AGPL-3.0).
