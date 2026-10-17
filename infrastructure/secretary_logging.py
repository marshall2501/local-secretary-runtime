# Provenance-Origin: original
# Provenance-AI: AI-generated
"""Product-wide local Secretary log configuration, independent of ports/UI."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path

from uvicorn.config import LOGGING_CONFIG
from uvicorn.logging import AccessFormatter, DefaultFormatter


class LocalMilliseconds:
    """Render a logging event's genuine subsecond timestamp."""

    def formatTime(self, record, datefmt=None) -> str:
        """Use the local timezone and the log record creation time."""
        return datetime.fromtimestamp(record.created).astimezone().isoformat(
            sep=" ", timespec="milliseconds",
        )


class SecretaryFormatter(LocalMilliseconds, DefaultFormatter):
    """Format startup and error records with actual event time."""


class SecretaryAccessFormatter(LocalMilliseconds, AccessFormatter):
    """Format HTTP method, URL, response code and actual event time."""


def build_secretary_log_config(*, log_dir: Path | None = None) -> dict:
    """Configure one console + rotating-file log for the Secretary process.

    Args:
        log_dir: Optional location for isolated tests. Defaults to local logs/.

    Returns:
        Independent Uvicorn dictConfig with shared Secretary file handler.
    """
    directory = log_dir if log_dir is not None else (
        Path(__file__).resolve().parents[1] / "logs"
    )
    directory.mkdir(parents=True, exist_ok=True)
    config = deepcopy(LOGGING_CONFIG)
    for name, formatter_class in (
        ("default", SecretaryFormatter),
        ("access", SecretaryAccessFormatter),
    ):
        formatter = config["formatters"][name]
        formatter["()"] = formatter_class
        formatter["fmt"] = "%(asctime)s [secretary] " + formatter["fmt"]
    config["formatters"]["secretary_file"] = {
        "()": SecretaryFormatter,
        "fmt": "%(asctime)s [secretary] %(levelprefix)s %(message)s",
        "use_colors": False,
    }
    config["handlers"]["secretary_file"] = {
        "class": "logging.handlers.RotatingFileHandler",
        "formatter": "secretary_file",
        "filename": str(directory / "secretary.log"),
        "encoding": "utf-8",
        "maxBytes": 10 * 1024 * 1024,
        "backupCount": 5,
        "delay": True,
    }
    for name in ("uvicorn", "uvicorn.access"):
        config["loggers"][name]["handlers"].append("secretary_file")
    return config
