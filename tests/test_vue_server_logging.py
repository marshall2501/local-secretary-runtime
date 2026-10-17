# Provenance-Origin: original
# Provenance-AI: AI-generated
"""Offline checks for the daily Vue Uvicorn console log format."""
from __future__ import annotations

import logging
import re
import unittest

from uvicorn.config import LOGGING_CONFIG
from uvicorn.logging import AccessFormatter, DefaultFormatter

from interfaces.vue_daily.server_logging import build_vue_log_config


TIMESTAMP = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [+-]\d{4}"


class VueServerLoggingTests(unittest.TestCase):
    """Check both real Uvicorn log formatter classes without starting servers."""

    def test_timestamped_error_and_access_logs(self):
        config = build_vue_log_config()
        default = config["formatters"]["default"]
        error_formatter = DefaultFormatter(
            fmt=default["fmt"], datefmt=default["datefmt"], use_colors=False,
        )
        error_record = logging.LogRecord(
            "uvicorn.error", logging.INFO, __file__, 1,
            "Application startup complete.", (), None,
        )
        formatted_error = error_formatter.format(error_record)
        self.assertRegex(
            formatted_error, rf"^{TIMESTAMP} INFO:\s+Application startup complete\.$",
        )

        access = config["formatters"]["access"]
        access_formatter = AccessFormatter(
            fmt=access["fmt"], datefmt=access["datefmt"], use_colors=False,
        )
        access_record = logging.LogRecord(
            "uvicorn.access", logging.INFO, __file__, 1,
            '%s - "%s %s HTTP/%s" %d',
            ("127.0.0.1:5555", "GET", "/api/v1/pkb/memory/search?limit=1",
             "1.1", 200), None,
        )
        formatted_access = access_formatter.format(access_record)
        self.assertRegex(formatted_access, rf"^{TIMESTAMP} INFO:\s+127\.0\.0\.1:")
        self.assertIn('"GET /api/v1/pkb/memory/search?limit=1 HTTP/1.1"', formatted_access)
        self.assertTrue(formatted_access.endswith("200 OK"))

    def test_uvicorn_defaults_and_http_log_handlers_are_unchanged(self):
        original_default = LOGGING_CONFIG["formatters"]["default"]["fmt"]
        original_access = LOGGING_CONFIG["formatters"]["access"]["fmt"]
        config = build_vue_log_config()
        config["formatters"]["default"]["fmt"] = "overridden locally"
        self.assertEqual(LOGGING_CONFIG["formatters"]["default"]["fmt"], original_default)
        self.assertEqual(LOGGING_CONFIG["formatters"]["access"]["fmt"], original_access)
        self.assertEqual(config["handlers"], LOGGING_CONFIG["handlers"])
        self.assertEqual(config["loggers"], LOGGING_CONFIG["loggers"])


if __name__ == "__main__":
    unittest.main()
