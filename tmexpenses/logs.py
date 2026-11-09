"""Logging setup. LOG_LEVEL is debug, info (default), warning or error.

The app logs under the "tmexpenses" logger, which is also Flask's own app.logger, so a
crash's traceback goes to the same place. Under gunicorn it shares gunicorn's handlers (and so
its format); gunicorn's own level comes from the same LOG_LEVEL via gunicorn.conf.py.

Never log tokens, secrets, notes, vendors, amounts or invoice contents.
"""
import logging

LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR}
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def parse_level(raw):
    name = (raw or "").strip().lower()
    if not name:
        return logging.INFO
    if name not in LEVELS:
        raise ValueError(f"LOG_LEVEL must be one of {', '.join(LEVELS)} (got {raw!r}).")
    return LEVELS[name]


def configure(logger, raw):
    """Set the level and make sure the records are written somewhere."""
    logger.setLevel(parse_level(raw))
    gunicorn = logging.getLogger("gunicorn.error")
    if gunicorn.handlers:
        logger.handlers = gunicorn.handlers
        logger.propagate = False
    elif not logging.getLogger().handlers and not logger.handlers:   # e.g. `flask run`
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(FORMAT))
        logger.addHandler(handler)
