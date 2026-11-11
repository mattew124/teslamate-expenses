-- Made-up expenses for trying the app on a dev stack: `scripts/dev.sh sample-expenses`.
-- Run on the dev test database only, never on a real TeslaMate one. `tz` is the app's timezone
-- (APP_TZ), so the times of day come out as written below.
--
-- They go on the first car in the test database and are dated back from today, so they spread over
-- the history copied from TeslaMate. The odometer is looked up from those positions the way the
-- app does it, so the oldest ones (before TeslaMate tracked the car) are left blank. Every note
-- starts with 'Sample data', which is how `scripts/dev.sh sample-expenses clear` finds them again.
-- Nothing here is real: the vendors are invented and the invoice links go nowhere.

BEGIN;

DELETE FROM expenses.expense WHERE notes LIKE 'Sample data%';

INSERT INTO expenses.expense (car_id, category_id, occurred_at, amount, odometer_km, vendor, notes, invoice_url)
WITH s AS (
  SELECT row_number() OVER (ORDER BY days_ago DESC, category) AS n, *
  FROM (VALUES
    -- days ago, hour, category, amount, vendor, invoice link?, note
    (1100,  10, 'Initial purchase', 64990.00, 'Example EV Sales',                          true,  'Delivery, before TeslaMate tracked it'),
    (1090,  10, 'Initial purchase', -3000.00, 'Example State EV Rebate',                   true,  'A rebate: a negative amount lowers the total'),
    (1085,  14, 'Insurance',         1512.40, 'Sample Mutual Insurance',                   true,  NULL),
    (1080,  11, 'Registration',       790.10, 'Example Transport Department',              false, NULL),
    (730,    9, 'Insurance',         1588.20, 'Sample Mutual Insurance',                   true,  NULL),
    (720,   15, 'Registration',       812.45, 'Example Transport Department',              true,  NULL),
    (560,   13, 'Cabin air filter',    62.30, 'Example Auto Parts',                        false, NULL),
    (560,   13, 'Air filter',          48.75, 'Example Auto Parts',                        false, 'Bought together with the cabin filter'),
    (400,   10, 'Tyres',             1246.50, 'Example Coastal Tyre, Brakes & Auto Electrical Specialists Pty Ltd', true, 'A long vendor name, to see how it is cut short'),
    (399,   10, 'Wheel alignment',    139.00, 'Example Coastal Tyre, Brakes & Auto Electrical Specialists Pty Ltd', true, NULL),
    (365,    9, 'Insurance',         1642.80, 'Sample Mutual Insurance',                   true,  NULL),
    (355,   16, 'Registration',       834.90, 'Example Transport Department',              true,  NULL),
    (200,   11, 'Tyre rotation',       89.00, 'Sample Tyre Centre',                        false, NULL),
    (120,   12, 'Cabin air filter',    67.85, 'Example Auto Parts',                        true,  NULL),
    (45,    14, 'Tyre rotation',       95.50, 'Sample Tyre Centre',                        true,  NULL),
    (12,    10, 'Air filter',          41.15, 'Example Auto Parts',                        false, NULL),
    (6,      9, 'Wheel alignment',    149.90, 'Sample Tyre Centre',                        true,  'Check the toe after the kerb strike'),
    (3,     15, 'Insurance',         1701.35, 'Sample Mutual Insurance',                   true,  NULL)
  ) AS v(days_ago, hour, category, amount, vendor, invoice, note)
)
SELECT car.id, cat.id, at.moment, s.amount,
       (SELECT round(p.odometer::numeric, 1) FROM public.positions p
         WHERE p.car_id = car.id AND p.odometer IS NOT NULL AND p.date <= (at.moment AT TIME ZONE 'UTC')
         ORDER BY p.date DESC LIMIT 1),
       s.vendor,
       'Sample data' || COALESCE('. ' || s.note, ''),
       CASE WHEN s.invoice THEN 'https://example.com/sample-invoice-' || s.n || '.pdf' END
FROM s
CROSS JOIN LATERAL (SELECT id FROM public.cars ORDER BY id LIMIT 1) AS car
JOIN expenses.category cat ON cat.name = s.category
CROSS JOIN LATERAL (SELECT (date_trunc('day', now() AT TIME ZONE :'tz') - make_interval(days => s.days_ago) + make_interval(hours => s.hour)) AT TIME ZONE :'tz' AS moment) AS at;

COMMIT;
