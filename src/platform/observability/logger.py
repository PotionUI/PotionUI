"""The one logging setup for the backend process.

`configure_logging()` is called once from the entry point (`api.py`), which is
imported both by `python api.py` and by `uvicorn api:app`, so the two ways of
starting PotionUI produce the same lines in the same places: a Rich console
handler and a size-rotated file at `storage/logs/potionui.log`.

Importing this module configures nothing and creates no directory - the log
directory appears when the app starts, not when a pipe imports `logger`.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path

from rich.logging import RichHandler

FILE_FORMAT = "%(asctime)s | %(levelname)8s | %(name)s | %(message)s"
FILE_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

CONSOLE_FORMAT = "%(message)s"
CONSOLE_DATE_FORMAT = "[%X]"

LOG_FILE_NAME = "potionui.log"
DEFAULT_LOG_DIR = "storage/logs"
DEFAULT_MAX_BYTES = 20 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 10

# Values of POTIONUI_LOG_FILE that turn the file handler off, for a read-only
# filesystem or a test run that must not write anywhere.
_FILE_OFF = frozenset({"off", "none", "no", "0", "false"})

# Set on the handlers this module installs, so a second call recognizes its own
# work and adds nothing. Handlers installed by anything else are left alone.
_CONSOLE_MARK = "_potionui_console_handler"
_FILE_MARK = "_potionui_file_handler"

# Uvicorn installs its own handlers on these and turns propagation off, which
# would keep server and access lines out of the file. Emptying them and letting
# them propagate puts every line through the same two handlers.
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")

logger = logging.getLogger("is")


def resolve_log_level() -> int:
    """The level named by `POTIONUI_LOG_LEVEL`, or INFO."""
    name = os.environ.get("POTIONUI_LOG_LEVEL", "INFO").strip().upper()
    level = getattr(logging, name, None)
    return level if isinstance(level, int) else logging.INFO


def log_directory() -> Path | None:
    """Where the log file belongs, or None when the file handler is off."""
    if os.environ.get("POTIONUI_LOG_FILE", "").strip().lower() in _FILE_OFF:
        return None
    configured = os.environ.get("POTIONUI_LOG_DIR")
    if configured is None:
        return Path(DEFAULT_LOG_DIR)
    if not configured.strip():
        return None
    return Path(configured.strip())


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _marked(root: logging.Logger, mark: str) -> logging.Handler | None:
    for handler in root.handlers:
        if getattr(handler, mark, False):
            return handler
    return None


def _build_console_handler(level: int) -> logging.Handler:
    handler = RichHandler()
    handler.setFormatter(logging.Formatter(CONSOLE_FORMAT, datefmt=CONSOLE_DATE_FORMAT))
    handler.setLevel(level)
    setattr(handler, _CONSOLE_MARK, True)
    return handler


def _build_file_handler(level: int) -> logging.Handler | None:
    directory = log_directory()
    if directory is None:
        return None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            directory / LOG_FILE_NAME,
            maxBytes=_int_env("POTIONUI_LOG_MAX_BYTES", DEFAULT_MAX_BYTES),
            backupCount=_int_env("POTIONUI_LOG_BACKUP_COUNT", DEFAULT_BACKUP_COUNT),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning(
            f"File logging disabled: {directory / LOG_FILE_NAME} is not writable ({exc}). "
            f"Set POTIONUI_LOG_DIR to a writable directory, or POTIONUI_LOG_FILE=off to silence this."
        )
        return None
    handler.setFormatter(logging.Formatter(FILE_FORMAT, datefmt=FILE_DATE_FORMAT))
    handler.setLevel(level)
    setattr(handler, _FILE_MARK, True)
    return handler


def configure_logging() -> logging.Logger:
    """Install the console and rotating-file handlers on the root logger.

    Idempotent: a second call finds the handlers it installed and returns
    without adding duplicates.
    """
    root = logging.getLogger()
    level = resolve_log_level()
    root.setLevel(level)

    if _marked(root, _CONSOLE_MARK) is None:
        root.addHandler(_build_console_handler(level))

    if _marked(root, _FILE_MARK) is None:
        file_handler = _build_file_handler(level)
        if file_handler is not None:
            root.addHandler(file_handler)

    for name in _UVICORN_LOGGERS:
        uvicorn_logger = logging.getLogger(name)
        for handler in list(uvicorn_logger.handlers):
            uvicorn_logger.removeHandler(handler)
        uvicorn_logger.propagate = True

    return logger
