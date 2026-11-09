"""The dev stack's one job is to stay away from live data. Guard that, the
generated schema snapshot, and the rules keeping the app behind a login."""

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEV = (ROOT / "docker-compose.dev.yml").read_text()
DEV_PROXY = (ROOT / "docker-compose.dev.traefik.yml").read_text()
PROD_PROXY = (ROOT / "docker-compose.traefik.yml").read_text()
SCRIPT = (ROOT / "scripts" / "dev.sh").read_text()
SCHEMA = (ROOT / "db" / "teslamate-schema.sql").read_text()


def config():
    out = subprocess.run(
        ["docker", "compose", "-f", str(ROOT / "docker-compose.dev.yml"), "--env-file", "/dev/null",
         "config", "--format", "json", "--no-interpolate"],
        check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def test_dev_stack_only_has_its_own_network():
    cfg = config()
    assert set(cfg["networks"]) == {"dev"}
    assert "external" not in cfg["networks"]["dev"]
    assert "teslamate_default" not in DEV + DEV_PROXY


def test_test_database_is_never_published():
    db = config()["services"]["dev-db"]
    assert set(db["networks"]) == {"dev"} and "ports" not in db
    assert "dev-db:" not in DEV_PROXY


def test_host_ports_are_loopback_only():
    for name in ("expenses-dev", "dev-grafana"):
        (port,) = config()["services"][name]["ports"]
        assert port.startswith("127.0.0.1:")


def test_every_router_requires_a_forward_auth_middleware():
    for text in (DEV_PROXY, PROD_PROXY):
        routers = set(re.findall(r"routers\.(\S+?)\.rule=", text))
        assert routers
        for router in routers:
            assert re.search(rf"routers\.{re.escape(router)}\.middlewares=\$\{{AUTH_MIDDLEWARE:\?", text), router


def test_dev_routes_outrank_production():
    prod = int(re.search(r"tm-expenses-secure\.priority=(\d+)", PROD_PROXY).group(1))
    dev = [int(p) for p in re.findall(r"\.priority=\$\{DEV_\w+_PRIORITY:-(\d+)\}", DEV_PROXY)]
    assert len(dev) == 2 and prod < min(dev)
    # Worktree priorities come from dev.sh; tests/test_worktrees.py checks their order.


def test_dev_routes_end_in_a_slash():
    """Otherwise /expenses-dev/foo would also serve /expenses-dev/foobar."""
    rules = re.findall(r"PathPrefix\(`([^`]+)`\)", DEV_PROXY)
    assert rules and all(r.endswith("/") for r in rules)


def test_only_reads_from_the_seed_source():
    sql = re.findall(r'source_psql "([^"]*)"', SCRIPT)
    assert sql
    for statement in sql:
        assert statement.startswith(("SELECT ", "COPY (SELECT ")), statement
        if statement.startswith("COPY"):
            assert statement.endswith("TO STDOUT"), statement
    # The only other command against the source is the schema-only dump.
    execs = [l for l in SCRIPT.splitlines() if "docker exec" in l and "SEED_FROM" in l]
    assert len(execs) == 2 and "--schema-only" in execs[1]


def test_positions_are_hourly_and_stripped_of_location():
    for blanked in ("WHEN 'latitude'  THEN '0'", "WHEN 'longitude' THEN '0'", "WHEN 'elevation' THEN 'NULL'"):
        assert blanked in SCRIPT
    assert "date_trunc('hour', date)" in SCRIPT
    assert "location data reached the test database" in SCRIPT


def test_schema_snapshot_is_the_real_definition():
    for column in ("latitude numeric(8,6) NOT NULL", "tpms_pressure_rr", "marketing_name",
                   "charge_energy_used", "position_id integer NOT NULL"):
        assert column in SCHEMA
    assert "REVOKE ALL ON SCHEMA public FROM PUBLIC" in SCHEMA
    targets = re.findall(r"REFERENCES public\.(\w+)\(", SCHEMA)
    assert targets and set(targets) == {"cars"}


def test_schema_filter():
    dump = r"""--
-- PostgreSQL database dump
--
\restrict abc123
SET statement_timeout = 0;
SELECT pg_catalog.set_config('search_path', '', false);

CREATE TABLE public.cars (
    id smallint NOT NULL
);


ALTER TABLE ONLY public.cars
    ADD CONSTRAINT cars_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.cars
    ADD CONSTRAINT cars_settings_id_fkey FOREIGN KEY (settings_id) REFERENCES public.car_settings(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.positions
    ADD CONSTRAINT positions_car_id_fkey FOREIGN KEY (car_id) REFERENCES public.cars(id) ON DELETE CASCADE;
"""
    out = subprocess.run(["awk", "-f", str(ROOT / "scripts" / "filter-schema-dump.awk")],
                         input=dump, capture_output=True, text=True, check=True).stdout
    assert "restrict" not in out and "SET " not in out and "pg_catalog" not in out and "--" not in out
    assert "cars_pkey" in out and "positions_car_id_fkey" in out and "car_settings" not in out
    assert "ALTER TABLE ONLY public.cars\n    ADD CONSTRAINT cars_pkey" in out


def test_dashboard_has_no_hardcoded_timezone():
    dash = json.loads((ROOT / "grafana" / "cost-of-ownership.json").read_text())
    assert any(v["name"] == "tz" for v in dash["templating"]["list"])
    sql = [t["rawSql"] for p in dash["panels"] for t in p["targets"]]
    assert any("AT TIME ZONE '$tz'" in s for s in sql)
    assert not any(re.search(r"AT TIME ZONE '[A-Z][a-z]+/", s) for s in sql)


def test_dashboard_expenses_link_comes_from_the_apps_own_settings():
    """A bare "/expenses/" resolves against Grafana's own host. The link is read from the
    URL saved on the app's Settings page instead."""
    dash = json.loads((ROOT / "grafana" / "cost-of-ownership.json").read_text())
    (link,) = [l for l in dash["links"] if l.get("title") == "Add / edit expenses"]
    assert link["url"] == "${expenses_url}"
    var = next(v for v in dash["templating"]["list"] if v["name"] == "expenses_url")
    assert "expenses.setting" in var["query"]


def test_seeded_grafana_link_points_at_this_dashboards_uid():
    from tmexpenses import db
    dash = json.loads((ROOT / "grafana" / "cost-of-ownership.json").read_text())
    assert dash["uid"] == db.DASHBOARD_UID


def test_dashboard_units_never_auto_scale():
    """Grafana's km and currency units rescale: 57,197 km became "57 Mm", $1,359 became "$1K"."""
    raw = (ROOT / "grafana" / "cost-of-ownership.json").read_text()
    assert "lengthkm" not in raw and "currencyUSD" not in raw


def test_dev_script_syntax():
    subprocess.run(["bash", "-n", str(ROOT / "scripts" / "dev.sh")], check=True)


def onedrive_link_section():
    start = SCRIPT.index("onedrive_link() {")
    return SCRIPT[start:SCRIPT.index("\n}\n", start)]


def test_onedrive_link_never_copies_a_real_folder():
    """Worktrees share the main dev stack's sign-in, never the folder it's pointed at."""
    link = onedrive_link_section()
    assert "expenses.onedrive_account" in link
    source_reads = [line for line in link.splitlines() if "COPY (SELECT" in line]
    assert source_reads and all("car_folder" not in line for line in source_reads)
    assert "DELETE FROM expenses.car_folder" in link


def test_onedrive_link_only_runs_from_a_worktree():
    assert 'if [ -z "$INSTANCE" ]' in onedrive_link_section()


def test_onedrive_link_only_reads_the_source_stack():
    for line in onedrive_link_section().splitlines():
        if "source_dev_psql" in line:
            assert not re.search(r"(?i)\b(insert|update|delete|truncate)\b", line), line


def test_real_uploads_are_kept_to_the_test_folder():
    browser = (ROOT / "tests" / "browser" / "run.py").read_text()
    assert 'TEST_FOLDER = "Expenses test"' in browser
    helper = (ROOT / "scripts" / "onedrive-test-folder.py").read_text()
    assert 'FOLDER = "Expenses test"' in helper

