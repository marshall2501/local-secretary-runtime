"""Composition root for the daily web runtime.

Only this module binds application ports to concrete PostgreSQL adapters.
Interfaces import these factories instead of importing psycopg or postgres
adapters directly.
"""
from __future__ import annotations

from infrastructure.async_runtime.transport import request_json_with_retry as _request_json_with_retry
import ritsuko.magi.async_execution as _magi_async_execution


def configure_magi_async_transport() -> None:
    _magi_async_execution.request_json_with_retry = _request_json_with_retry


configure_magi_async_transport()

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
from infrastructure.postgres.magi_task_repository import PostgresMagiTaskRepository
from infrastructure.postgres.magi_settings_repository import PostgresMagiSettingsRepository
from infrastructure.postgres.service_connection_repository import PostgresServiceConnectionRepository
from infrastructure.postgres.service_billing_settings_repository import PostgresServiceBillingSettingsRepository
from infrastructure.postgres.finance_repository import PostgresFinanceRepository
from infrastructure.postgres.core_task_query_repository import PostgresCoreTaskQueryRepository
from infrastructure.postgres.entity_catalog_repository import PostgresEntityCatalogRepository
from infrastructure.postgres.pkb_repository import PostgresPkbRepository
from infrastructure.postgres.pkb_debug import debug_database_summary
from infrastructure.postgres.pkb_runtime import DBNAME, HOST, WRITER, connect_pkb_database
from ritsuko.application.task_queries import CoreTaskQueryService
from pkb.application.entity_catalog import EntityCatalogService


def connection():
    return connect_pkb_database()


def build_core_execution_repository() -> PostgresCoreExecutionRepository:
    return PostgresCoreExecutionRepository(connection)


def build_core_task_queries() -> CoreTaskQueryService:
    return CoreTaskQueryService(PostgresCoreTaskQueryRepository(connection))


def build_entity_catalog_service() -> EntityCatalogService:
    return EntityCatalogService(PostgresEntityCatalogRepository(connection))


def build_pkb_repository() -> PostgresPkbRepository:
    return PostgresPkbRepository(connection)


def build_magi_task_repository() -> PostgresMagiTaskRepository:
    return PostgresMagiTaskRepository(connection)


def build_magi_settings_repository() -> PostgresMagiSettingsRepository:
    return PostgresMagiSettingsRepository(connection)


def build_service_connection_repository() -> PostgresServiceConnectionRepository:
    return PostgresServiceConnectionRepository(connection)


def build_service_billing_settings_repository() -> PostgresServiceBillingSettingsRepository:
    return PostgresServiceBillingSettingsRepository(connection)


def build_finance_repository() -> PostgresFinanceRepository:
    return PostgresFinanceRepository(connection)


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
