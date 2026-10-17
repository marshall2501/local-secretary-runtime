# Provenance-Origin: original
# Provenance-AI: AI-generated
"""Time-stamped Uvicorn console logging for owner-local Vue servers.

Keep Uvicorn's normal error/access loggers, handlers and HTTP fields intact.
The dates follow the host PC's local timezone, not a forced UTC timezone.
"""
from __future__ import annotations

from copy import deepcopy

from uvicorn.config import LOGGING_CONFIG


def build_vue_log_config() -> dict:
    """Add host-local timestamps to startup/error and HTTP access log lines.

    Returns:
        dict: Independent Uvicorn dictConfig that does not mutate its defaults.
    """
    config = deepcopy(LOGGING_CONFIG)
    for name in ("default", "access"):
        formatter = config["formatters"][name]
        formatter["fmt"] = "%(asctime)s " + formatter["fmt"]
        formatter["datefmt"] = "%Y-%m-%d %H:%M:%S %z"
    return config
