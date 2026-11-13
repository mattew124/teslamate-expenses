"""Describing a document in a log line, without revealing what is in it.

The log level and handlers are set up once for the "garagebooks" logger (which is Flask's app
logger); the loggers used here are its children and inherit both. A line names the file, its size,
a short hash, how its text was read and which fields were found: never the text, amounts or dates.
"""

import hashlib
import time


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
