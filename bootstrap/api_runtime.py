"""Composition root for the restricted FastAPI runtime."""
from __future__ import annotations

import os
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row

from application.read_service import ReadService
from infrastructure.postgres.candidate_repository import PostgresCandidateRepository
from infrastructure.postgres.read_repository import PostgresReadRepository
from infrastructure.postgres.task_repository import PostgresTaskRepository
from pkb.candidate_service import CandidateService
from ritsuko.tasks.service import TaskService


def api_config() -> tuple[str, str]:
    dsn = os.getenv("LSA_API_DSN", "").strip()
    token = os.getenv("LSA_API_TOKEN", "")
    container_mode = os.getenv("LSA_API_CONTAINER_MODE", "") == "1"
    token_file = os.getenv("LSA_API_TOKEN_FILE", "")
    password_file = os.getenv("LSA_API_DB_PASSWORD_FILE", "")
    if not dsn:
        raise RuntimeError("Set LSA_API_DSN for a dedicated restricted DB login.")
    if token_file:
        if not container_mode or token:
            raise RuntimeError("Token file requires container mode without inline token.")
        try:
            token = Path(token_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RuntimeError("Unable to read API token secret file.") from exc
    if len(token) < 32:
        raise RuntimeError("Set a unique LSA_API_TOKEN of at least 32 characters.")
    parsed = conninfo_to_dict(dsn)
    if parsed.get("user") in ("secretary_admin", "postgres", None, ""):
        raise RuntimeError("Refusing PostgreSQL provisioning/superuser account.")
    if parsed.get("hostaddr"):
        raise RuntimeError("Host address override is not allowed.")
    if container_mode:
        if parsed.get("user") != "secretary_api":
            raise RuntimeError("Container API requires the candidate-only secretary_api login.")
        if parsed.get("host") != "secretary-postgres":
            raise RuntimeError("Container API must use its dedicated DB service hostname.")
        if not password_file or parsed.get("password"):
            raise RuntimeError("Container mode requires a separate DB password file.")
        try:
            password = Path(password_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RuntimeError("Unable to read database password secret file.") from exc
        if not password:
            raise RuntimeError("Database password file is empty.")
        dsn = make_conninfo(dsn, password=password)
    elif parsed.get("host") not in ("localhost", "127.0.0.1", "::1"):
        raise RuntimeError("Desktop MVP API requires a localhost DB connection.")
    elif password_file:
        raise RuntimeError("Password file mode requires container mode.")
    return dsn, token


def connect() -> psycopg.Connection:
    dsn, _ = api_config()
    return psycopg.connect(dsn, row_factory=dict_row, connect_timeout=5)


def build_read_service(connect_factory=connect) -> ReadService:
    return ReadService(PostgresReadRepository(connect_factory))


def build_task_service(connect_factory=connect) -> TaskService:
    return TaskService(PostgresTaskRepository(connect_factory))


def build_candidate_service(connect_factory=connect) -> CandidateService:
    return CandidateService(PostgresCandidateRepository(connect_factory))


def verify_api_database(connect_factory=connect) -> None:
    """Fail closed unless the configured API login matches its restricted contract."""
    with connect_factory() as db:
        with db.cursor() as cur:
            cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
            row = cur.fetchone()
            if row is None or row["rolsuper"]:
                raise RuntimeError("API DB login must be a non-superuser.")
            cur.execute(
                "SELECT pg_has_role(current_user, %s, 'member') AS too_privileged",
                ("secretary_memory_writer",),
            )
            if cur.fetchone()["too_privileged"]:
                raise RuntimeError("API must not inherit trusted memory-write privileges.")
            checks = (
                ("secretary.entities", "SELECT"),
                ("secretary.pending_claims", "INSERT"),
                ("secretary.tasks", "INSERT"),
                ("secretary.audit_events", "INSERT"),
            )
            for table, permission in checks:
                if table == "secretary.pending_claims":
                    cur.execute(
                        "SELECT has_column_privilege(current_user, %s, %s, 'INSERT') AS permitted",
                        (table, "entity_id"),
                    )
                else:
                    cur.execute(
                        "SELECT has_table_privilege(current_user, %s, %s) AS permitted",
                        (table, permission),
                    )
                if not cur.fetchone()["permitted"]:
                    raise RuntimeError(
                        f"API DB login is missing {permission} on {table}."
                    )
