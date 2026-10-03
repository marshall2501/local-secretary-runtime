"""Offline API guardrail tests: no running database or user data required."""
import os
import unittest
from unittest.mock import patch

from pydantic import ValidationError
from interfaces.api.app import (
    CreateTask, NewCandidate, TaskTransition, api_config, external_read_token,
    read_authenticated,
)


class ApiStaticTests(unittest.TestCase):
    def test_refuse_missing_configuration(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                api_config()

    def test_refuse_admin_and_remote_database(self):
        for dsn in (
            "host=127.0.0.1 user=secretary_admin dbname=secretary",
            "host=192.0.2.10 user=secretary_api dbname=secretary",
        ):
            with self.subTest(dsn=dsn), patch.dict(
                os.environ, {"LSA_API_DSN": dsn, "LSA_API_TOKEN": "x" * 48},
                clear=True
            ):
                with self.assertRaises(RuntimeError):
                    api_config()

    def test_require_long_token(self):
        with patch.dict(
            os.environ,
            {"LSA_API_DSN": "host=127.0.0.1 user=secretary_api dbname=secretary",
             "LSA_API_TOKEN": "short"},
            clear=True,
        ):
            with self.assertRaises(RuntimeError):
                api_config()

    def test_validate_api_configuration(self):
        with patch.dict(
            os.environ,
            {"LSA_API_DSN": "host=127.0.0.1 user=secretary_api dbname=secretary",
             "LSA_API_TOKEN": "x" * 48},
            clear=True,
        ):
            self.assertEqual(api_config()[1], "x" * 48)

    def test_external_read_token_is_optional(self):
        with patch.dict(
            os.environ,
            {"LSA_API_DSN": "host=127.0.0.1 user=secretary_api dbname=secretary",
             "LSA_API_TOKEN": "x" * 48},
            clear=True,
        ):
            self.assertIsNone(external_read_token())

    def test_external_read_token_must_be_distinct(self):
        with patch.dict(
            os.environ,
            {"LSA_API_DSN": "host=127.0.0.1 user=secretary_api dbname=secretary",
             "LSA_API_TOKEN": "x" * 48,
             "LSA_EXTERNAL_READ_TOKEN": "x" * 48},
            clear=True,
        ):
            with self.assertRaises(RuntimeError):
                external_read_token()

    def test_external_read_token_authenticates_read_dependency_only(self):
        with patch.dict(
            os.environ,
            {"LSA_API_DSN": "host=127.0.0.1 user=secretary_api dbname=secretary",
             "LSA_API_TOKEN": "x" * 48,
             "LSA_EXTERNAL_READ_TOKEN": "r" * 48},
            clear=True,
        ):
            header = "Bearer " + "r" * 48
            self.assertEqual(read_authenticated(header), "external_reader")
            with self.assertRaises(Exception):
                # Write routes continue to depend on authenticated(), not this helper.
                from interfaces.api.app import authenticated
                authenticated(header)

    def test_task_requires_completion_criteria(self):
        with self.assertRaises(ValidationError):
            CreateTask(request="check PC", domain="pc", completion_criteria="")

    def test_revision_must_be_nonnegative(self):
        with self.assertRaises(ValidationError):
            TaskTransition(expected_revision=-1)

    def test_candidate_confidence_bounded(self):
        with self.assertRaises(ValidationError):
            NewCandidate(
                entity_id="00000000-0000-0000-0000-000000000001",
                source_id="00000000-0000-0000-0000-000000000002",
                claim_type="observation", predicate="performance",
                proposed_value={"state": "slow"}, confidence=1.01,
                evidence="tool observation", extraction_model="local",
                prompt_version="v1"
            )


if __name__ == "__main__":
    unittest.main()
