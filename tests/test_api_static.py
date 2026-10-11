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

    def test_tasks_accepts_legacy_status_filter_without_changing_api_default(self):
        from unittest.mock import Mock
        from fastapi import HTTPException
        from interfaces.api.app import app, tasks

        route = next(route for route in app.routes
                     if getattr(route, "path", None) == "/tasks"
                     and "GET" in getattr(route, "methods", set()))
        parameter_names = {field.name for field in route.dependant.query_params}
        self.assertIn("task_status", parameter_names)
        self.assertIn("status", parameter_names)
        self.assertIn("limit", parameter_names)

        fake_service = Mock()
        fake_service.list_tasks.return_value = []
        with patch("interfaces.api.app.read_service", return_value=fake_service):
            self.assertEqual(tasks(task_status="running", status=None, limit=10), [])
            fake_service.list_tasks.assert_called_with(task_status="running", limit=10)
            self.assertEqual(tasks(task_status=None, status="pending", limit=20), [])
            fake_service.list_tasks.assert_called_with(task_status="pending", limit=20)
            self.assertEqual(tasks(task_status="completed", status="completed", limit=3), [])
            fake_service.list_tasks.assert_called_with(task_status="completed", limit=3)
            self.assertEqual(tasks(task_status=None, status=None, limit=5), [])
            fake_service.list_tasks.assert_called_with(task_status=None, limit=5)
            previous_calls = fake_service.list_tasks.call_count
            with self.assertRaises(HTTPException) as error:
                tasks(task_status="running", status="completed", limit=10)
            self.assertEqual(error.exception.status_code, 422)
            self.assertEqual(fake_service.list_tasks.call_count, previous_calls)

    def test_external_read_query_limits_remain_bounded(self):
        from fastapi.routing import APIRoute
        from interfaces.api.app import app
        for endpoint in ("/tasks", "/memory/search"):
            with self.subTest(endpoint=endpoint):
                route = next(route for route in app.routes
                             if isinstance(route, APIRoute)
                             and route.path == endpoint and "GET" in route.methods)
                limit = next(param for param in route.dependant.query_params
                             if param.name == "limit")
                self.assertTrue(any(getattr(meta, "le", None) == 100
                                    for meta in limit.field_info.metadata))

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
