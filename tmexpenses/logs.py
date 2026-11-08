"""Logging to stdout/stderr, for docker's own log capture (and whatever collects it).

LOG_LEVEL (debug, info, warning, error; default info) sets how much the app says. At debug each
document is described: its name, size, a short hash, how its text was read and which fields were
found. Never its text, amounts or dates: those can be personal.
"""

import hashlib
import logging
import os
import time

LEVELS = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARNING": logging.WARNING, "ERROR": logging.ERROR}
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure():
    """Set up the 'tmexpenses' logger once, however many times the app is built."""
    name = (os.environ.get("LOG_LEVEL") or "INFO").strip().upper()
    log = logging.getLogger("tmexpenses")
    log.setLevel(LEVELS.get(name, logging.INFO))
    if not any(getattr(h, "_tmexpenses", False) for h in log.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(FORMAT))
        handler._tmexpenses = True
        log.addHandler(handler)
    if name and name not in LEVELS:
        log.warning("LOG_LEVEL=%r isn't one of debug, info, warning, error: using info", name)


def sha12(data):
    return hashlib.sha256(data).hexdigest()[:12]


def quote(name):
    """A file name safe to put in one log line."""
    return '"' + (name or "").replace('"', "'").replace("\n", " ").replace("\r", " ")[:200] + '"'


def found(guess):
    """Names of the fields a proposal filled in (never their values) and of those it couldn't."""
    keys = ("date", "category", "vendor", "amount", "odometer_km")
    have = [k for k in keys if guess.get(k) not in (None, "")]
    return ",".join(have) or "-", ",".join(k for k in keys if k not in have) or "-"


class Timer:
    def __init__(self):
        self.start = time.monotonic()

    @property
    def ms(self):
        return int((time.monotonic() - self.start) * 1000)
