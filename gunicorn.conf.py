# Gunicorn reads this from the working directory (/srv in the image).
import os

_level = os.environ.get("LOG_LEVEL", "info").strip().lower()
# An unknown value is reported by the app itself on start; don't also stop gunicorn here.
loglevel = _level if _level in ("debug", "info", "warning", "error") else "info"
