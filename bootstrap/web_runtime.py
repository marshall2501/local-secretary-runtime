"""Composition root for the daily web runtime.

Only this module binds application ports to concrete PostgreSQL adapters.
Interfaces import these factories instead of importing psycopg or postgres
adapters directly.
"""
from __future__ import annotations

from psycopg import Error as DatabaseError

from infrastructure.postgres.core_advisor_repository import (
    claim_cooperative_probe,
    fail_cooperative_probe,
    finalize_cooperative_probe,
    list_interrupted_advisors,
    record_cooperative_probe,
    restore_interrupted_cooperative_probe,
    write_advisor_shadow,
)
from infrastructure.postgres.core_execution_repository import PostgresCoreExecutionRepository
from infrastructure.postgres.core_task_query_repository import PostgresCoreTaskQueryRepository
from infrastructure.postgres.pkb_debug import debug_database_summary
from infrastructure.postgres.pkb_runtime import DBNAME, HOST, WRITER, connect_pkb_database
from ritsuko.application.task_queries import CoreTaskQueryService


def connection():
    return connect_pkb_database()


def build_core_execution_repository() -> PostgresCoreExecutionRepository:
    return PostgresCoreExecutionRepository(connection)


def build_core_task_queries() -> CoreTaskQueryService:
    return CoreTaskQueryService(PostgresCoreTaskQueryRepository(connection))


def write_core_advisor_shadow(task_id, shadow, event_type):
    return write_advisor_shadow(connection, task_id, shadow, event_type)


def claim_core_cooperative_probe(task_id, capability):
    return claim_cooperative_probe(connection, task_id, capability)


def record_core_cooperative_probe(task_id, request, execution, observation_pack):
    return record_cooperative_probe(connection, task_id, request, execution, observation_pack)


def finalize_core_cooperative_probe(task_id, final_decision, final_observation_pack, execution):
    return finalize_cooperative_probe(connection, task_id, final_decision, final_observation_pack, execution)


def fail_core_cooperative_probe(task_id, error):
    return fail_cooperative_probe(connection, task_id, error)


def restore_core_cooperative_probe(task_id, error):
    return restore_interrupted_cooperative_probe(connection, task_id, error)


def interrupted_core_advisors():
    return list_interrupted_advisors(connection)


def pkb_debug_summary() -> dict:
    return debug_database_summary(
        connection,
        expected_database=DBNAME,
        expected_user=WRITER,
    )
