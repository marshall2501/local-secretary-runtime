# Provenance-Origin: original
# Provenance-AI: AI-generated
"""Offline checks for genuine millisecond timestamps in Vue server logs."""
from __future__ import annotations

import logging
import re
import unittest

from uvicorn.config import LOGGING_CONFIG

from interfaces.vue_daily.server_logging import (
    TimestampedAccessFormatter,
    TimestampedDefaultFormatter,
    build_vue_log_config,
)


TIMESTAMP = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2}"


class VueServerLoggingTests(unittest.TestCase):
    """Check Uvicorn access/error formatting without real servers or databases."""

    def test_timestamped_error_and_access_logs(self):
        config = build_vue_log_config()
        default = config["formatters"]["default"]
        self.assertIs(default["()"], TimestampedDefaultFormatter)
        error_formatter = TimestampedDefaultFormatter(
            fmt=default["fmt"], use_colors=False,
        )
        error_record = logging.LogRecord(
            "uvicorn.error", logging.INFO, __file__, 1,
            "Application startup complete.", (), None,
        )
        # The fractional event timestamp really exists: preserve .234 rather
        # than appending a constant .000 or formatting time of test execution.
        error_record.created = 1770000000.234
        formatted_error = error_formatter.format(error_record)
        self.assertRegex(
            formatted_error, rf"^{TIMESTAMP} INFO:\s+Application startup complete\.$",
        )
        self.assertIn(".234", formatted_error)

        access = config["formatters"]["access"]
        self.assertIs(access["()"], TimestampedAccessFormatter)
        access_formatter = TimestampedAccessFormatter(
            fmt=access["fmt"], use_colors=False,
        )
        access_record = logging.LogRecord(
            "uvicorn.access", logging.INFO, __file__, 1,
            '%s - "%s %s HTTP/%s" %d',
            ("127.0.0.1:5555", "GET", "/api/v1/pkb/memory/search?limit=1",
             "1.1", 200), None,
        )
        access_record.created = 1770000000.567
        formatted_access = access_formatter.format(access_record)
        self.assertRegex(formatted_access, rf"^{TIMESTAMP} INFO:\s+127\.0\.0\.1:")
        self.assertIn(".567", formatted_access)
        self.assertIn('"GET /api/v1/pkb/memory/search?limit=1 HTTP/1.1"', formatted_access)
        self.assertTrue(formatted_access.endswith("200 OK"))

    def test_uvicorn_defaults_and_http_handlers_are_unchanged(self):
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
