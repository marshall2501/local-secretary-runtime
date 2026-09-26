"""Minimal, local-only Secretary API. No LLM or external tool execution.

The database login MUST be separately provisioned with restricted group roles.
The API never accepts memory-review decisions or external-action requests.
"""
from __future__ import annotations

import hmac
import os
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, StringConstraints


Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
ClaimType = Literal["fact", "observation", "attribute", "unconfirmed"]
TaskStatus = Literal[
    "pending", "running", "waiting_approval", "waiting_external",
    "paused", "completed", "failed", "cancelled",
]


class CreateTask(BaseModel):
    request: Text
    domain: ShortText
    completion_criteria: Text
    entity_id: UUID | None = None
    due_at: datetime | None = None


class TaskTransition(BaseModel):
    expected_revision: int = Field(ge=0)


class NewCandidate(BaseModel):
    entity_id: UUID
    source_id: UUID
    claim_type: ClaimType
    predicate: ShortText
    proposed_value: dict | list | str | int | float | bool | None
    confidence: Decimal | None = Field(default=None, ge=0, le=1)
    evidence: Text
    extraction_model: ShortText
    prompt_version: ShortText


def api_config() -> tuple[str, str]:
    dsn = os.getenv("LSA_API_DSN", "").strip()
    token = os.getenv("LSA_API_TOKEN", "")
    if not dsn:
        raise RuntimeError("Set LSA_API_DSN for a dedicated restricted DB login.")
    if len(token) < 32:
        raise RuntimeError("Set a unique LSA_API_TOKEN of at least 32 characters.")
    parsed = conninfo_to_dict(dsn)
    if parsed.get("user") in ("secretary_admin", "postgres"):
        raise RuntimeError("Refusing PostgreSQL provisioning/superuser account.")
    if parsed.get("host") not in ("localhost", "127.0.0.1", "::1"):
        raise RuntimeError("MVP API requires a local PostgreSQL connection.")
    return dsn, token


def connect() -> psycopg.Connection:
    dsn, _ = api_config()
    return psycopg.connect(dsn, row_factory=dict_row, connect_timeout=5)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Fail closed at startup; do not offer an API backed by an admin connection.
    with connect() as db:
        with db.cursor() as cur:
            cur.execute(
                "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
            )
            row = cur.fetchone()
            if row is None or row["rolsuper"]:
                raise RuntimeError("API DB login must be a non-superuser.")
            checks = (
                ("secretary.entities", "SELECT"),
                ("secretary.pending_claims", "INSERT"),
                ("secretary.tasks", "INSERT"),
                ("secretary.audit_events", "INSERT"),
            )
            for table, permission in checks:
                if table == "secretary.pending_claims":
                    # Candidate role has column-level INSERT only, not table INSERT.
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
    yield


app = FastAPI(
    title="Local Secretary API (MVP)",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


def authenticated(authorization: Annotated[str | None, Header()] = None) -> str:
    _, expected = api_config()
    candidate = authorization or ""
    if not hmac.compare_digest(candidate, f"Bearer {expected}"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid local API token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return "local_user"


@app.get("/healthz")
def healthz():
    with connect() as db:
        with db.cursor() as cur:
            cur.execute("SELECT 1 AS ok")
            cur.fetchone()
    return {"status": "ok"}


@app.get("/entities", dependencies=[Depends(authenticated)])
def entities(
    domain: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=100),
):
    with connect() as db:
        with db.cursor() as cur:
            cur.execute(
                """SELECT id, name, entity_type, domain, created_at, retired_at
                   FROM secretary.entities
                   WHERE (%s IS NULL OR domain = %s)
                   ORDER BY created_at DESC, id DESC LIMIT %s""",
                (domain, domain, limit),
            )
            return cur.fetchall()


@app.get("/claims/current", dependencies=[Depends(authenticated)])
def current_claims(
    entity_id: UUID,
    verified_only: bool = False,
    limit: int = Query(default=100, ge=1, le=100),
):
    with connect() as db:
        with db.cursor() as cur:
            cur.execute(
                """SELECT id, entity_id, source_id, claim_type, predicate, value,
                          evidence, origin, verification_status, valid_from,
                          valid_to, recorded_at
                   FROM secretary.current_claims
                   WHERE entity_id = %s
                     AND (NOT %s OR verification_status = 'verified')
                   ORDER BY recorded_at DESC, id DESC LIMIT %s""",
                (entity_id, verified_only, limit),
            )
            return cur.fetchall()


@app.get("/tasks", dependencies=[Depends(authenticated)])
def tasks(
    task_status: TaskStatus | None = None,
    limit: int = Query(default=50, ge=1, le=100),
):
    with connect() as db:
        with db.cursor() as cur:
            cur.execute(
                """SELECT id, request, requested_by, domain, completion_criteria,
                          status, due_at, next_run_at, checkpoint, revision,
                          created_at, updated_at, completed_at
                   FROM secretary.tasks
                   WHERE (%s IS NULL OR status = %s)
                   ORDER BY created_at DESC, id DESC LIMIT %s""",
                (task_status, task_status, limit),
            )
            return cur.fetchall()


@app.post("/tasks", status_code=201)
def create_task(body: CreateTask, actor: str = Depends(authenticated)):
    with connect() as db:
        with db.cursor() as cur:
            cur.execute(
                """INSERT INTO secretary.tasks
                   (request, requested_by, domain, completion_criteria,
                    entity_id, due_at, status)
                   VALUES (%s, %s, %s, %s, %s, %s, 'pending')
                   RETURNING id, status, revision, created_at""",
                (
                    body.request, actor, body.domain, body.completion_criteria,
                    body.entity_id, body.due_at,
                ),
            )
            created = cur.fetchone()
            cur.execute(
                """INSERT INTO secretary.audit_events
                   (actor, event_type, object_type, object_id)
                   VALUES (%s, 'task.created', 'task', %s)""",
                (actor, created["id"]),
            )
            return created


def transition_task(
    task_id: UUID,
    revision: int,
    expected_statuses: tuple[str, ...],
    destination: str,
    actor: str,
):
    with connect() as db:
        with db.cursor() as cur:
            cur.execute(
                """UPDATE secretary.tasks
                   SET status = %s
                   WHERE id = %s AND revision = %s AND status = ANY(%s)
                   RETURNING id, status, revision, updated_at""",
                (destination, task_id, revision, list(expected_statuses)),
            )
            updated = cur.fetchone()
            if updated is None:
                raise HTTPException(
                    status_code=409,
                    detail="Task absent, revision changed, or transition not allowed.",
                )
            cur.execute(
                """INSERT INTO secretary.audit_events
                   (actor, event_type, task_id, object_type, object_id)
                   VALUES (%s, %s, %s, 'task', %s)""",
                (actor, f"task.{destination}", task_id, task_id),
            )
            return updated


@app.post("/tasks/{task_id}/pause")
def pause_task(
    task_id: UUID,
    body: TaskTransition,
    actor: str = Depends(authenticated),
):
    return transition_task(
        task_id, body.expected_revision, ("pending", "running"), "paused", actor
    )


@app.post("/tasks/{task_id}/resume")
def resume_task(
    task_id: UUID,
    body: TaskTransition,
    actor: str = Depends(authenticated),
):
    # Resumption is pending, not automatic execution.
    return transition_task(
        task_id, body.expected_revision, ("paused",), "pending", actor
    )


@app.post("/pending-claims", status_code=201)
def propose_claim(body: NewCandidate, actor: str = Depends(authenticated)):
    # The restricted DB role cannot set reviewer/status, insert accepted
    # claims, or issue external approvals. A trusted review service is next.
    with connect() as db:
        with db.cursor() as cur:
            cur.execute(
                """INSERT INTO secretary.pending_claims
                   (entity_id, source_id, claim_type, predicate, proposed_value,
                    confidence, evidence, extraction_model, prompt_version)
                   VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
                   RETURNING id, review_status, recorded_at""",
                (
                    body.entity_id, body.source_id, body.claim_type,
                    body.predicate, Jsonb(body.proposed_value),
                    body.confidence, body.evidence, body.extraction_model,
                    body.prompt_version,
                ),
            )
            created = cur.fetchone()
            cur.execute(
                """INSERT INTO secretary.audit_events
                   (actor, event_type, object_type, object_id)
                   VALUES (%s, 'memory.candidate_proposed', 'pending_claim', %s)""",
                (actor, created["id"]),
            )
            return created
