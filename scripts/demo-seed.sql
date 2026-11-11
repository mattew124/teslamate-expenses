-- Invented TeslaMate data for the demo stack: `scripts/dev.sh demo up`.
--
-- Run on the demo test database only, after its public tables were created from a schema-only dump
-- of the live TeslaMate. Nothing here is copied from a real database: one made-up car, a made-up
-- odometer reading four times a day and made-up charging sessions, all worked out from the day
-- number so a rebuild gives the same picture. Dates run back from today, so the history always
-- ends now. Latitude and longitude are 0, as in the dev stack: there is no location history.
--
-- It only writes cars, positions and charging_processes (the three tables the app reads). The
-- sample expenses go on top of this (scripts/dev-sample-expenses.sql), starting 1100 days back.

BEGIN;

TRUNCATE public.positions, public.charging_processes, public.cars RESTART IDENTITY CASCADE;

INSERT INTO public.cars (id, eid, vid, model, efficiency, inserted_at, updated_at, vin, name,
                         trim_badging, settings_id, exterior_color, spoiler_type, wheel_type,
                         display_priority, marketing_name)
VALUES (1, 1000000000001, 1000000000001, '3', 0.153,
        now() AT TIME ZONE 'UTC' - interval '1099 days', now() AT TIME ZONE 'UTC',
        '5YJ3DEMO0000000001', 'Demo Model 3', 'Long Range', 1, 'DeepBlue', 'None', 'Pinwheel18', 1,
        'Model 3 Long Range');
SELECT setval('public.cars_id_seq', 1);

-- Four readings a day for 1,099 days. Distance per day: a weekday commute, longer weekends, and a
-- few long trips, so the odometer climbs unevenly. It starts a little above zero (a delivery drive).
INSERT INTO public.positions (id, date, latitude, longitude, odometer, battery_level, usable_battery_level, car_id)
WITH days AS (
    SELECT d,
           round((CASE WHEN extract(isodow FROM current_date - d) >= 6 THEN 46 ELSE 31 END
                  + 14 * abs(sin(d * 1.7))
                  + CASE WHEN d % 61 = 0 THEN 380 ELSE 0 END)::numeric, 1) AS km
    FROM generate_series(1099, 1, -1) AS d
), cumulative AS (
    SELECT d, 14 + sum(km) OVER (ORDER BY d DESC) - km AS start_km, km FROM days
)
SELECT row_number() OVER (ORDER BY c.d DESC, q.part),
       (date_trunc('day', now() AT TIME ZONE 'UTC') - make_interval(days => c.d) + make_interval(hours => q.part * 6)),
       0, 0,
       round((c.start_km + c.km * q.part / 4.0)::numeric, 1),
       (45 + 40 * abs(sin(c.d * 0.9 + q.part)))::int,
       (45 + 40 * abs(sin(c.d * 0.9 + q.part)))::int,
       1
FROM cumulative c CROSS JOIN generate_series(0, 3) AS q(part);
SELECT setval('public.positions_id_seq', (SELECT max(id) FROM public.positions));

-- About three sessions in seven days. Most are at home overnight (cheap, slow); roughly one in five is
-- a fast charger on a longer trip (dearer, and bigger top-ups). Energy used is a little over energy added.
INSERT INTO public.charging_processes (start_date, end_date, charge_energy_added, charge_energy_used,
                                       start_battery_level, end_battery_level, duration_min,
                                       start_rated_range_km, end_rated_range_km,
                                       start_ideal_range_km, end_ideal_range_km,
                                       outside_temp_avg, car_id, position_id, cost)
WITH sessions AS (
    SELECT d, (d * 7 + 3) % 17 AS pick, (d % 5 = 0) AS fast
    FROM generate_series(1098, 1, -1) AS d
), shaped AS (
    SELECT d, fast,
           round((CASE WHEN fast THEN 26 + (pick * 2.1) ELSE 9 + (pick * 1.6) END)::numeric, 2) AS added
    FROM sessions WHERE pick < 7 OR fast
)
SELECT date_trunc('day', now() AT TIME ZONE 'UTC') - make_interval(days => d) + make_interval(hours => CASE WHEN fast THEN 14 ELSE 9 END),
       date_trunc('day', now() AT TIME ZONE 'UTC') - make_interval(days => d)
           + make_interval(hours => CASE WHEN fast THEN 14 ELSE 9 END, mins => (CASE WHEN fast THEN added * 1.4 ELSE added * 6 END)::int),
       added, round(added * 1.08, 2),
       GREATEST(8, 60 - (added * 1.6)::int), LEAST(100, 60 - (added * 1.6)::int + (added * 1.6)::int + 4),
       (CASE WHEN fast THEN added * 1.4 ELSE added * 6 END)::smallint,
       round(((GREATEST(8, 60 - (added * 1.6)::int)) * 5.1)::numeric, 2), round((LEAST(100, 64) * 5.1)::numeric, 2),
       round(((GREATEST(8, 60 - (added * 1.6)::int)) * 5.1)::numeric, 2), round((LEAST(100, 64) * 5.1)::numeric, 2),
       round((18 + 8 * sin(d / 58.0))::numeric, 1),
       1,
       -- the last reading up to midday that day
       (SELECT id FROM public.positions p
         WHERE p.date <= date_trunc('day', now() AT TIME ZONE 'UTC') - make_interval(days => d) + interval '12 hours'
         ORDER BY p.date DESC LIMIT 1),
       -- $0.27 a kWh at home, $0.62 at a fast charger
       round((added * 1.08 * CASE WHEN fast THEN 0.62 ELSE 0.27 END)::numeric, 2)
FROM shaped;

COMMIT;
