-- Least-privilege grants for the web app's role. Run as the superuser after
-- schema.sql. :app_role is supplied by scripts/provision-db.sh (psql -v).
-- The app can write expenses and add categories, and read the three things it
-- needs from TeslaMate — nothing else in the database.
GRANT USAGE ON SCHEMA expenses TO :"app_role";
GRANT SELECT, INSERT, UPDATE, DELETE ON expenses.expense TO :"app_role";
GRANT SELECT, INSERT ON expenses.category TO :"app_role";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA expenses TO :"app_role";
-- USAGE on public is not implied: the live TeslaMate DB has revoked it from PUBLIC.
GRANT USAGE ON SCHEMA public TO :"app_role";
GRANT SELECT ON public.cars TO :"app_role";
GRANT SELECT (car_id, date, odometer) ON public.positions TO :"app_role";
