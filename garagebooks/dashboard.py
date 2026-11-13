"""The Grafana dashboard ships inside the image and is written to DASHBOARD_DIR on start.

Grafana mounts that directory (a shared volume) read-only and provisions from it, so the
dashboard is always the one that matches the running schema, with nothing to import by hand.
Two values are filled in from this installation's settings: the timezone box defaults to
APP_TZ, the "Add / edit expenses" link falls back to the app's own BASE_PATH, and the time range starts
at your earliest data (Grafana has no "all time", so the app works it out each time it starts).
"""
import json
import os
import tempfile
from pathlib import Path

FILENAME = "cost-of-ownership.json"
# /srv/garagebooks/ beside /srv/grafana/ in the image; the repository layout is the same.
SOURCE = Path(__file__).resolve().parent.parent / "grafana" / FILENAME
_FALLBACK = "'/expenses/'"     # the dashboard's own placeholder for the app's address


def render(tz, base_path, source=SOURCE, start=None):
    dash = json.loads(Path(source).read_text())
    if start is not None:
        dash["time"] = {"from": start.strftime("%Y-%m-01T00:00:00.000Z"), "to": "now"}
    variables = {v["name"]: v for v in dash["templating"]["list"]}
    tzvar = variables["tz"]
    tzvar["query"] = tz
    tzvar["current"] = {"selected": False, "text": tz, "value": tz}
    tzvar["options"] = [{"selected": True, "text": tz, "value": tz}]
    link = variables["expenses_url"]
    here = "'" + (base_path or "").replace("'", "''") + "/'"
    for key in ("query", "definition"):
        if _FALLBACK not in link[key]:
            raise ValueError("The dashboard's app-link placeholder moved: update dashboard.py.")
        link[key] = link[key].replace(_FALLBACK, here)
    return dash


def export(directory, tz, base_path, source=SOURCE, start=None):
    """Write the dashboard if it differs from what's there. True if it wrote."""
    text = json.dumps(render(tz, base_path, source, start), indent=2) + "\n"
    target = Path(directory) / FILENAME
    if target.exists() and target.read_text() == text:
        return False
    # Write beside the target and rename, so Grafana never reads half a file.
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".dashboard-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return True
