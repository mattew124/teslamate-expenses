# Filters `pg_dump --schema-only -t cars -t positions -t charging_processes`
# into a stable file that can load on its own:
#  - drops comments, SET lines and psql meta-commands (pg_dump 18 adds a random
#    \restrict token that would change the file on every run);
#  - drops foreign keys except those onto public.cars. The others point at
#    tables that aren't copied, or at position rows the hourly sample leaves out;
#  - loads TeslaMate's enum columns (public.unit_of_length etc.) as text: pg_dump -t
#    leaves the types behind, and this project only reads the URL columns.
/^--/ || /^SET / || /^SELECT pg_catalog/ || /^\\/ { next }
/^ALTER TABLE ONLY / { held = $0; next }
held != "" {
    if (!/FOREIGN KEY/ || /REFERENCES public\.cars\(/) { print held; print }
    held = ""
    next
}
{ gsub(/public\.(unit_of_length|unit_of_temperature|unit_of_pressure|preferred_range)/, "text") }
/^$/ { if (blank++) next; print; next }
{ blank = 0; print }
