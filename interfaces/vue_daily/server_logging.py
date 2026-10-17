# Provenance-Origin: original
# Provenance-AI: AI-generated
"""Owner-local timestamps for Vue Uvicorn startup and HTTP access logs.

Use the actual LogRecord.created value, with milliseconds retained from
Python logging. Keep default Uvicorn handlers, levels and access fields.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime

from uvicorn.config import LOGGING_CONFIG
from uvicorn.logging import AccessFormatter, DefaultFormatter


class _LocalTimeMillis:
    """Render the event's actual local timestamp, not a fabricated .000 suffix."""

    def formatTime(self, record, datefmt=None) -> str:
        """Return local ISO time with event milliseconds and numeric UTC offset."""
        return datetime.fromtimestamp(record.created).astimezone().isoformat(
            sep=" ", timespec="milliseconds",
        )


class TimestampedDefaultFormatter(_LocalTimeMillis, DefaultFormatter):
    """Keep Uvicorn's INFO/error styling with precise event time."""


class TimestampedAccessFormatter(_LocalTimeMillis, AccessFormatter):
    """Keep Uvicorn's client/method/path/status fields with precise event time."""


def build_vue_log_config() -> dict:
    """Add millisecond-precision local timestamps without changing Uvicorn defaults.

    Returns:
        dict: Independent Uvicorn dictConfig, compatible with its normal handlers.
    """
    config = deepcopy(LOGGING_CONFIG)
    for name, formatter_class in (
        ("default", TimestampedDefaultFormatter),
        ("access", TimestampedAccessFormatter),
    ):
        formatter = config["formatters"][name]
        formatter["()"] = formatter_class
        formatter["fmt"] = "%(asctime)s " + formatter["fmt"]
    return config
