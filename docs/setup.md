# Setup

!!! danger "No login of its own"

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
3. **OneDrive (optional).** See [OneDrive uploads](onedrive.md).
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

<!-- screenshot: settings-page - The Settings page, with the app and dashboard addresses -->

## The database

There is nothing to run. On start the app creates its own `expenses` schema in the TeslaMate
database (idempotent, so upgrades apply themselves), and only ever writes inside it. It reads
`cars`, `positions` and `charging_processes`. This needs TeslaMate's database user, which
owns the database; Grafana already reads it the same way.

To give the app less than that, run `scripts/provision-db.sh` once. It creates a role that
can write expenses and read only `cars` and `positions(car_id, date, odometer)`. Point
`DATABASE_USER`/`DATABASE_PASS` at it and set `SKIP_MIGRATE=1`. Re-run the script on upgrades.
