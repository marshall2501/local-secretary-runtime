# Provenance-Origin: original
# Provenance-AI: AI-generated
"""Owner-local timestamps for Vue Uvicorn startup and HTTP access logs.

Use the actual LogRecord.created value, with milliseconds retained from
Python logging. Keep default Uvicorn handlers, levels and access fields.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path

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


def build_vue_log_config(
    server_name: str = "vue-readonly-8097", *, log_dir: Path | None = None,
) -> dict:
    """Configure console and rotating local-file Uvicorn logs for one server.

    Args:
        server_name: Owner-local process label; fixes the log file name.
        log_dir: Alternate directory for isolated tests. Default: repository logs/.

    Returns:
        dict: Independent Uvicorn dictConfig for startup, errors and HTTP access.

    The file logs are a local development aid, not a separate audit store.
    """
    if server_name not in {"vue-readonly-8097", "vue-task-8099"}:
        raise ValueError("unknown Vue server name")
    directory = log_dir if log_dir is not None else Path(__file__).resolve().parents[2] / "logs"
    directory.mkdir(parents=True, exist_ok=True)

    config = deepcopy(LOGGING_CONFIG)
    for name, formatter_class in (
        ("default", TimestampedDefaultFormatter),
        ("access", TimestampedAccessFormatter),
    ):
        formatter = config["formatters"][name]
        formatter["()"] = formatter_class
        formatter["fmt"] = f"%(asctime)s [{server_name}] " + formatter["fmt"]

    # Keep console behavior, and duplicate startup/errors + HTTP requests into
    # a single bounded file for the respective owner-local Vue process.
    config["formatters"]["local_file"] = {
        "()": TimestampedDefaultFormatter,
        "fmt": f"%(asctime)s [{server_name}] %(levelprefix)s %(message)s",
        "use_colors": False,
    }
    config["handlers"]["local_file"] = {
        "class": "logging.handlers.RotatingFileHandler",
        "formatter": "local_file",
        "filename": str(directory / f"{server_name}.log"),
        "encoding": "utf-8",
        "maxBytes": 10 * 1024 * 1024,
        "backupCount": 5,
        "delay": True,
    }
    config["loggers"]["uvicorn"]["handlers"].append("local_file")
    config["loggers"]["uvicorn.access"]["handlers"].append("local_file")
    return config
