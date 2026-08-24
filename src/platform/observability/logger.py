import logging
import os
from rich.logging import RichHandler

_log_level_name = os.environ.get("POTIONUI_LOG_LEVEL", "INFO").strip().upper()
_log_level = getattr(logging, _log_level_name, None)
if not isinstance(_log_level, int):
    _log_level = logging.INFO

logging.basicConfig(
    level=_log_level,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler()]
)
logger = logging.getLogger("is")
