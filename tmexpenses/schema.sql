-- teslamate-expenses schema. Idempotent: safe to re-run (scripts/provision-db.sh
-- does so on every deploy). Lives in its own `expenses` schema inside the
-- TeslaMate database so Grafana can join it to TeslaMate's own tables
-- (cars, positions, charging_processes) without a second datasource.
--
-- Run as the database superuser/owner, NOT as the app role: it creates the
-- schema, the foreign key onto public.cars and the reporting view.

CREATE SCHEMA IF NOT EXISTS expenses;

-- kind: 'purchase' = capital cost (what you paid for the car),
--       'running'  = everything spent keeping it on the road.
CREATE TABLE IF NOT EXISTS expenses.category (
    id        smallserial PRIMARY KEY,
    name      text NOT NULL UNIQUE CHECK (btrim(name) <> ''),
    kind      text NOT NULL DEFAULT 'running' CHECK (kind IN ('purchase', 'running')),
    sort_order integer NOT NULL DEFAULT 100
);

INSERT INTO expenses.category (name, kind, sort_order) VALUES
    ('Initial purchase',  'purchase',  10),
    ('Insurance',         'running',   20),
    ('Registration',      'running',   30),
    ('Tyres',             'running',   40),
    ('Cabin air filter',  'running',   50),
    ('Air filter',        'running',   60),
    ('Wheel alignment',   'running',   70),
    ('Tyre rotation',     'running',   80)
ON CONFLICT (name) DO NOTHING;

-- Settings, saved from the app's Settings page (the same idea as TeslaMate's own).
-- A single row. Both are optional, and http(s) only: they are rendered as links.
--   expenses_url  where this app is published; the Grafana dashboard links back to it
--   grafana_url   the Grafana dashboard for this app; the app's header links to it
CREATE TABLE IF NOT EXISTS expenses.setting (
    id            smallint PRIMARY KEY CHECK (id = 1),
    expenses_url  text CHECK (expenses_url IS NULL OR expenses_url ~* '^https?://[^[:space:]]+$'),
    grafana_url   text CHECK (grafana_url IS NULL OR grafana_url ~* '^https?://[^[:space:]]+$')
);
-- Added after the table first shipped, so existing databases get it too.
--   invoice_date_format  the date in uploaded invoice names (yyyy, yy, mm, dd); NULL = yymmdd
ALTER TABLE expenses.setting ADD COLUMN IF NOT EXISTS invoice_date_format text
    CHECK (invoice_date_format IS NULL OR length(invoice_date_format) <= 20);
INSERT INTO expenses.setting (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS expenses.expense (
    id           bigserial PRIMARY KEY,
    car_id       smallint NOT NULL REFERENCES public.cars (id),
    category_id  smallint NOT NULL REFERENCES expenses.category (id),
    -- Moment the cost was incurred (timestamptz; the UI enters it in APP_TZ).
    occurred_at  timestamptz NOT NULL,
    amount       numeric(12, 2) NOT NULL CHECK (amount >= 0),
    -- Odometer in km at occurred_at. Optional: costs from before TeslaMate
    -- started tracking the car have no position to look it up from.
    odometer_km  numeric(9, 1) CHECK (odometer_km IS NULL OR odometer_km >= 0),
    vendor       text,
    notes        text,
    -- Link to the PDF invoice (a cloud-storage share link). https only: it is rendered
    -- as an <a href>, so never allow javascript:/data: schemes in here.
    invoice_url  text CHECK (invoice_url IS NULL OR invoice_url ~* '^https://[^[:space:]]+$'),
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS expense_car_time_idx ON expenses.expense (car_id, occurred_at);
CREATE INDEX IF NOT EXISTS expense_category_idx ON expenses.expense (category_id);

-- OneDrive folder each car's invoice PDFs go to, chosen on the app's OneDrive
-- page. Uploads go by folder_id, so renaming or moving the folder in OneDrive
-- doesn't break them; folder_path is only for showing where that is.
CREATE TABLE IF NOT EXISTS expenses.car_folder (
    car_id      smallint PRIMARY KEY REFERENCES public.cars (id) ON DELETE CASCADE,
    folder_id   text NOT NULL CHECK (folder_id <> ''),
    folder_path text NOT NULL
);

-- The OneDrive connection: at most one row. client_id/client_secret are the
-- installer's own Entra app registration, entered on the OneDrive page.
-- client_secret, refresh_token and auth_state are encrypted by the app with
-- ONEDRIVE_TOKEN_KEY, so a database dump or backup on its own can't open the
-- drive. auth_state holds the state and PKCE verifier of a sign-in in progress.
CREATE TABLE IF NOT EXISTS expenses.onedrive_account (
    id              boolean PRIMARY KEY DEFAULT true CHECK (id),
    client_id       text,
    client_secret   text,
    secret_expires  date,
    account         text,
    refresh_token   text,
    auth_state      text,
    auth_expires_at timestamptz,
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- One row per car: everything the TCO dashboard's headline panels need, so the
-- definitions (what counts as "start odometer", what is charging) live in one
-- place instead of being copy-pasted across panels.
--   start_km    odometer when you bought it (purchase expense), else the first
--               odometer TeslaMate ever recorded
--   owned_since purchase date, else first TeslaMate position
CREATE OR REPLACE VIEW expenses.ownership AS
SELECT
    car.id                                         AS car_id,
    car.name                                       AS car_name,
    COALESCE(ex.purchase_total, 0)                 AS purchase_total,
    COALESCE(ex.running_total, 0)                  AS running_total,
    COALESCE(ch.charging_total, 0)                 AS charging_total,
    COALESCE(ex.purchase_total, 0) + COALESCE(ex.running_total, 0)
        + COALESCE(ch.charging_total, 0)           AS tco,
    COALESCE(ex.purchase_km, pos.first_km)         AS start_km,
    pos.last_km                                    AS current_km,
    -- GREATEST() ignores NULLs, so guard explicitly: no positions => no distance, not 0.
    CASE WHEN pos.last_km IS NULL THEN NULL
         ELSE GREATEST(pos.last_km - COALESCE(ex.purchase_km, pos.first_km), 0)
    END                                            AS distance_km,
    COALESCE(ex.purchase_at, pos.first_at AT TIME ZONE 'UTC') AS owned_since
FROM public.cars car
LEFT JOIN LATERAL (
    SELECT
        SUM(e.amount) FILTER (WHERE c.kind = 'purchase') AS purchase_total,
        SUM(e.amount) FILTER (WHERE c.kind = 'running')  AS running_total,
        (ARRAY_AGG(e.odometer_km ORDER BY e.occurred_at)
            FILTER (WHERE c.kind = 'purchase' AND e.odometer_km IS NOT NULL))[1] AS purchase_km,
        MIN(e.occurred_at) FILTER (WHERE c.kind = 'purchase') AS purchase_at
    FROM expenses.expense e
    JOIN expenses.category c ON c.id = e.category_id
    WHERE e.car_id = car.id
) ex ON true
LEFT JOIN LATERAL (
    SELECT SUM(cp.cost) AS charging_total
    FROM public.charging_processes cp
    WHERE cp.car_id = car.id
) ch ON true
LEFT JOIN LATERAL (
    SELECT
        MIN(p.odometer) AS first_km,
        MAX(p.odometer) AS last_km,
        MIN(p.date)     AS first_at
    FROM public.positions p
    WHERE p.car_id = car.id
) pos ON true;
