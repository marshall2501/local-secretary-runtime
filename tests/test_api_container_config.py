"""Offline guardrails for portable API container mode. No Docker needed."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from psycopg.conninfo import conninfo_to_dict

from api.secretary_api import api_config


class ApiContainerConfigTests(unittest.TestCase):
    def test_container_uses_separate_secret_files(self):
        with tempfile.TemporaryDirectory() as folder:
            token = Path(folder) / "token"
            password = Path(folder) / "password"
            token.write_text("t" * 48)
            password.write_text("not-a-real-password")
            env = {
                "LSA_API_DSN": "host=secretary-postgres port=5432 "
                               "dbname=secretary user=secretary_api",
                "LSA_API_CONTAINER_MODE": "1",
                "LSA_API_TOKEN_FILE": str(token),
                "LSA_API_DB_PASSWORD_FILE": str(password),
            }
            with patch.dict(os.environ, env, clear=True):
                dsn, result_token = api_config()
            self.assertEqual(result_token, "t" * 48)
            self.assertEqual(conninfo_to_dict(dsn)["password"], "not-a-real-password")

    def test_container_refuses_arbitrary_network_host(self):
        with patch.dict(os.environ, {
            "LSA_API_DSN": "host=evil-service user=secretary_api dbname=secretary",
            "LSA_API_TOKEN": "x" * 48,
            "LSA_API_CONTAINER_MODE": "1",
        }, clear=True):
            with self.assertRaises(RuntimeError):
                api_config()

    def test_container_requires_secret_not_inline_password(self):
        with patch.dict(os.environ, {
            "LSA_API_DSN": "host=secretary-postgres user=secretary_api "
                           "password=oops dbname=secretary",
            "LSA_API_TOKEN": "x" * 48,
            "LSA_API_CONTAINER_MODE": "1",
        }, clear=True):
            with self.assertRaises(RuntimeError):
                api_config()

    def test_hostaddr_cannot_bypass_localhost_constraint(self):
        with patch.dict(os.environ, {
            "LSA_API_DSN": "host=127.0.0.1 hostaddr=192.0.2.10 "
                           "user=secretary_api dbname=secretary",
            "LSA_API_TOKEN": "x" * 48,
        }, clear=True):
            with self.assertRaises(RuntimeError):
                api_config()

    def test_missing_file_fails_closed(self):
        with patch.dict(os.environ, {
            "LSA_API_DSN": "host=secretary-postgres user=secretary_api dbname=secretary",
            "LSA_API_CONTAINER_MODE": "1",
            "LSA_API_TOKEN_FILE": "/nonexistent/secret",
            "LSA_API_DB_PASSWORD_FILE": "/nonexistent/password",
        }, clear=True):
            with self.assertRaises(RuntimeError):
                api_config()


if __name__ == "__main__":
    unittest.main()
