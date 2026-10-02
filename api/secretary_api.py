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
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from psycopg.conninfo import conninfo_to_dict, make_conninfo
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


# G1: deterministic, LLM-free first slice. Later stages attach memory,
# simulated tools, and verification to these persisted task steps.
PROTOTYPE_STEPS = (
    "Recall: inspect existing memory and previous actions",
    "Plan: choose a safe read-only diagnostic",
    "Execute: run a simulated read-only diagnostic",
    "Verify and record the result",
)


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
    # Desktop dev mode keeps the existing localhost DSN + token workflow.
    # Container mode loads both secrets from files mounted by Compose.
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


def external_read_token() -> str | None:
    """Return the optional read-only external credential.

    Keeping this separate from api_config preserves the existing local API
    configuration contract. When absent, external-read access is disabled.
    """
    token = os.getenv("LSA_EXTERNAL_READ_TOKEN", "")
    token_file = os.getenv("LSA_EXTERNAL_READ_TOKEN_FILE", "")
    container_mode = os.getenv("LSA_API_CONTAINER_MODE", "") == "1"
    if token_file:
        if not container_mode or token:
            raise RuntimeError(
                "External token file requires container mode without inline token."
            )
        try:
            token = Path(token_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RuntimeError("Unable to read external-read token secret file.") from exc
    else:
        token = token.strip()
    if not token:
        return None
    if len(token) < 32:
        raise RuntimeError(
            "Set LSA_EXTERNAL_READ_TOKEN to at least 32 characters when enabled."
        )
    _, local_token = api_config()
    if hmac.compare_digest(token, local_token):
        raise RuntimeError("External-read token must differ from the local API token.")
    return token


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


def read_authenticated(authorization: Annotated[str | None, Header()] = None) -> str:
    """Accept the normal credential or the optional external read-only one."""
    candidate = authorization or ""
    _, local_token = api_config()
    if hmac.compare_digest(candidate, f"Bearer {local_token}"):
        return "local_user"
    external_token = external_read_token()
    if external_token and hmac.compare_digest(candidate, f"Bearer {external_token}"):
        return "external_reader"
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid API token.",
        headers={"WWW-Authenticate": "Bearer"},
    )


@app.get("/healthz")
def healthz():
    with connect() as db:
        with db.cursor() as cur:
            cur.execute("SELECT 1 AS ok")
            cur.fetchone()
    return {"status": "ok"}


@app.get("/entities", dependencies=[Depends(read_authenticated)])
def entities(
    domain: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=100),
):
    with connect() as db:
        with db.cursor() as cur:
            cur.execute(
                """SELECT id, name, entity_type, domain, created_at, retired_at
                   FROM secretary.entities
                   WHERE (%s::text IS NULL OR domain = %s)
                   ORDER BY created_at DESC, id DESC LIMIT %s""",
                (domain, domain, limit),
            )
            return cur.fetchall()


@app.get("/claims/current", dependencies=[Depends(read_authenticated)])
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


@app.get("/tasks", dependencies=[Depends(read_authenticated)])
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
                   WHERE (%s::text IS NULL OR status = %s)
                   ORDER BY created_at DESC, id DESC LIMIT %s""",
                (task_status, task_status, limit),
            )
            return cur.fetchall()


# Structured SQL enumeration is the default; this endpoint does not use RAG,
# vector similarity, LLM summarization or a top-k approximation.
MEMORY_KINDS = ("claim", "issue", "hypothesis", "source")


def memory_search_sql(include_history: bool) -> str:
    # Only our own constant relation names may be substituted into SQL.
    claim_relation = "secretary.claims" if include_history else "secretary.current_claims"
    return f"""
        WITH memory_records AS (
          SELECT c.id, 'claim'::text AS kind, e.domain, e.name AS entity_name,
                 c.predicate AS title, c.value::text AS value_text,
                 c.evidence AS evidence, c.verification_status AS state,
                 s.id AS source_id, s.citation AS source_citation, s.uri AS source_uri,
                 c.recorded_at AS recorded_at
          FROM {claim_relation} c
          JOIN secretary.entities e ON e.id=c.entity_id
          JOIN secretary.sources s ON s.id=c.source_id
          UNION ALL
          SELECT i.id, 'issue', e.domain, e.name,
                 i.description, NULL::text, NULL::text, i.status,
                 s.id, s.citation, s.uri, i.recorded_at
          FROM secretary.issues i
          LEFT JOIN secretary.entities e ON e.id=i.entity_id
          JOIN secretary.sources s ON s.id=i.source_id
          UNION ALL
          SELECT h.id, 'hypothesis', e.domain, e.name,
                 h.statement, NULL::text, h.evidence, h.status,
                 s.id, s.citation, s.uri, h.recorded_at
          FROM secretary.hypotheses h
          JOIN secretary.issues i ON i.id=h.issue_id
          LEFT JOIN secretary.entities e ON e.id=i.entity_id
          JOIN secretary.sources s ON s.id=h.source_id
          UNION ALL
          SELECT s.id, 'source', NULL::text, NULL::text,
                 s.citation, NULL::text, NULL::text, s.source_type,
                 s.id, s.citation, s.uri, s.recorded_at
          FROM secretary.sources s
        )
        SELECT * FROM memory_records
        WHERE (%s::text IS NULL OR domain=%s)
          AND (%s::text IS NULL OR kind=%s)
          AND (%s::text IS NULL OR
               strpos(lower(concat_ws(' ', entity_name, title, value_text,
                                      evidence, source_citation, source_uri)),
                      lower(%s)) > 0)
    """


@app.get("/memory/search", dependencies=[Depends(read_authenticated)])
def search_memory(
    q: str | None = Query(default=None, max_length=200),
    domain: str | None = Query(default=None, max_length=200),
    kind: Literal["claim", "issue", "hypothesis", "source"] | None = None,
    include_history: bool = False,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=1000000),
):
    # q omitted => unfiltered enumeration. q supplied => exact substring
    # matching over stored data, NOT semantic similarity. Each response
    # includes a total and a stable (within one DB snapshot) result page.
    q = q.strip() or None if q is not None else None
    domain = domain.strip() or None if domain is not None else None
    filters = (domain, domain, kind, kind, q, q)
    matching_sql = memory_search_sql(include_history)
    with connect() as db:
        with db.cursor() as cur:
            cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            cur.execute(
                f"SELECT count(*) AS total FROM ({matching_sql}) AS matching",
                filters,
            )
            total = cur.fetchone()["total"]
            cur.execute(
                f"""SELECT * FROM ({matching_sql}) AS matching
                    ORDER BY recorded_at DESC, kind ASC, id DESC
                    LIMIT %s OFFSET %s""",
                (*filters, limit, offset),
            )
            items = cur.fetchall()
    return {
        "total": total, "limit": limit, "offset": offset,
        "include_history": include_history, "items": items,
    }



@app.get("/experience/search", dependencies=[Depends(read_authenticated)])
def search_experience(
    domain: str | None = Query(default=None, max_length=200),
    entity_name: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=1000000),
):
    """Read past actions and observed results with explicit target linkage.

    An older task without entity_id is returned as unlinked, NEVER silently
    attributed to an entity merely because it has the same domain. The
    records describe actual saved operations, which may be simulated.
    """
    domain = domain.strip() or None if domain is not None else None
    entity_name = entity_name.strip() or None if entity_name is not None else None
    if entity_name and not domain:
        raise HTTPException(status_code=422, detail="entity_name requires domain")
    where = """
        FROM secretary.actions a
        JOIN secretary.tasks t ON t.id = a.task_id
        LEFT JOIN secretary.entities e ON e.id = t.entity_id
        LEFT JOIN secretary.results r ON r.action_id = a.id
        LEFT JOIN secretary.sources s ON s.id = r.source_id
        WHERE (%s::text IS NULL OR t.domain = %s)
          AND (%s::text IS NULL OR e.name = %s)
    """
    filters = (domain, domain, entity_name, entity_name)
    with connect() as db:
        with db.cursor() as cur:
            cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            cur.execute("SELECT count(*) AS total " + where, filters)
            total = cur.fetchone()["total"]
            cur.execute(
                """SELECT a.id AS action_id, t.id AS task_id,
                          t.domain, t.entity_id, e.name AS entity_name,
                          t.request AS task_request, t.status AS task_status,
                          a.tool, a.operation, a.status AS action_status,
                          a.parameters, a.started_at, a.finished_at,
                          r.id AS result_id, r.outcome, r.summary, r.evidence,
                          r.recorded_at, s.citation AS source_citation
                """ + where + """
                ORDER BY a.started_at DESC NULLS LAST, a.id DESC
                LIMIT %s OFFSET %s
                """,
                (*filters, limit, offset),
            )
            items = cur.fetchall()
    return {
        "total": total, "limit": limit, "offset": offset,
        "scope": "linked entity only" if entity_name else
                 "domain-wide including unlinked tasks",
        "items": items,
    }


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



@app.post("/prototype/tasks", status_code=201)
def create_prototype_task(body: CreateTask, actor: str = Depends(authenticated)):
    """Create one real Task ID and the fixed G1 steps in one DB transaction.

    This is planning/persistence only: NO tool execution or LLM is implied.
    An ordinary user need not manually insert Task Steps or copy UUIDs.
    """
    with connect() as db:
        with db.cursor() as cur:
            cur.execute(
                """INSERT INTO secretary.tasks
                   (request, requested_by, domain, completion_criteria,
                    entity_id, due_at, status)
                   VALUES (%s, %s, %s, %s, %s, %s, 'pending')
                   RETURNING id, status, revision, created_at""",
                (body.request, actor, body.domain, body.completion_criteria,
                 body.entity_id, body.due_at),
            )
            created = cur.fetchone()
            steps = []
            for step_order, description in enumerate(PROTOTYPE_STEPS):
                cur.execute(
                    """INSERT INTO secretary.task_steps
                       (task_id, step_order, description)
                       VALUES (%s, %s, %s)
                       RETURNING id, step_order, description, status""",
                    (created["id"], step_order, description),
                )
                steps.append(cur.fetchone())
            cur.execute(
                """INSERT INTO secretary.audit_events
                   (actor, event_type, task_id, object_type, object_id)
                   VALUES (%s, 'prototype.task_created', %s, 'task', %s)""",
                (actor, created["id"], created["id"]),
            )
    return {**created, "steps": steps, "phase": "planned"}



@app.post("/prototype/tasks/{task_id}/run")
def run_prototype_task(task_id: UUID, actor: str = Depends(authenticated)):
    """G1 deterministic slice: recall -> fixed plan -> mock tool -> verify/record.

    Never diagnoses or modifies a real PC. All writes commit atomically; a
    repeated call to an already completed prototype returns its saved summary.
    Memory auto-write, LLM, Research, and a real Worker are separate G1/G2 work.
    """
    with connect() as db:
        with db.cursor() as cur:
            cur.execute(
                """SELECT id, domain, entity_id, completion_criteria, status, checkpoint
                   FROM secretary.tasks WHERE id = %s FOR UPDATE""",
                (task_id,),
            )
            task = cur.fetchone()
            if task is None:
                raise HTTPException(status_code=404, detail="Task not found.")
            checkpoint = task["checkpoint"] or {}
            if task["status"] in ("completed", "waiting_external") and "g1" in checkpoint:
                return checkpoint["g1"]
            if task["status"] != "pending":
                raise HTTPException(
                    status_code=409,
                    detail="Prototype can only run pending tasks.",
                )

            cur.execute(
                """SELECT id, step_order, status
                   FROM secretary.task_steps WHERE task_id = %s
                   ORDER BY step_order FOR UPDATE""",
                (task_id,),
            )
            steps = cur.fetchall()
            if (len(steps) != len(PROTOTYPE_STEPS)
                    or [row["step_order"] for row in steps] != list(range(len(PROTOTYPE_STEPS)))
                    or any(row["status"] != "pending" for row in steps)):
                raise HTTPException(status_code=409, detail="Not a fresh G1 prototype task.")

            # SQL recall is deliberately bounded for the live demonstration;
            # /memory/search remains the separate exhaustive/paginated API.
            cur.execute(
                """SELECT c.id, e.name AS entity_name, c.predicate, c.value,
                          c.verification_status, s.citation
                   FROM secretary.current_claims c
                   JOIN secretary.entities e ON e.id = c.entity_id
                   JOIN secretary.sources s ON s.id = c.source_id
                   WHERE e.domain = %s
                     AND (%s::uuid IS NULL OR c.entity_id = %s::uuid)
                   ORDER BY c.recorded_at DESC, c.id DESC LIMIT 20""",
                (task["domain"], task["entity_id"], task["entity_id"]),
            )
            recalled = [
                {**row, "id": str(row["id"])} for row in cur.fetchall()
            ]
            cur.execute(
                """SELECT r.id, r.outcome, r.summary, a.tool, a.operation
                   FROM secretary.results r
                   JOIN secretary.actions a ON a.id = r.action_id
                   JOIN secretary.tasks t ON t.id = a.task_id
                   WHERE t.domain = %s AND t.id <> %s
                   ORDER BY r.recorded_at DESC, r.id DESC LIMIT 10""",
                (task["domain"], task_id),
            )
            previous_results = [
                {**row, "id": str(row["id"])} for row in cur.fetchall()
            ]
            cur.execute(
                """UPDATE secretary.task_steps
                   SET status = 'completed', checkpoint = %s
                   WHERE id = %s""",
                (Jsonb({"recalled_claim_count": len(recalled),
                        "previous_result_count": len(previous_results)}), steps[0]["id"]),
            )
            cur.execute(
                """UPDATE secretary.task_steps
                   SET status = 'completed', checkpoint = %s
                   WHERE id = %s""",
                (Jsonb({"plan": "simulated_read_only_diagnostic"}), steps[1]["id"]),
            )

            # This Source describes GENERATED test output, not real machine
            # evidence; a sha256/file archive would add no value here.
            cur.execute(
                """INSERT INTO secretary.sources
                   (source_type, uri, citation, retrieved_at, confidentiality, metadata)
                   VALUES ('tool', %s, %s, now(), 'private', %s)
                   RETURNING id""",
                (f"tool://prototype/simulated-read-only/{task_id}",
                 "G1 simulated diagnostic; no real PC was inspected",
                 Jsonb({"prototype": True, "simulated": True})),
            )
            source_id = cur.fetchone()["id"]
            cur.execute(
                """INSERT INTO secretary.actions
                   (task_id, step_id, actor, tool, operation, parameters,
                    risk, authorization_basis, status, idempotency_key,
                    reversible, started_at, finished_at)
                   VALUES (%s, %s, %s, 'prototype_mock', 'simulated_read_only',
                           %s, 'read_only', 'prototype_fixture_only', 'succeeded',
                           %s, true, now(), now())
                   RETURNING id""",
                (task_id, steps[2]["id"], actor,
                 Jsonb({"simulated": True}), f"g1:{task_id}:simulated_read_only"),
            )
            action_id = cur.fetchone()["id"]
            result_summary = "Simulated diagnostic recorded; no actual PC inspected."
            cur.execute(
                """INSERT INTO secretary.results
                   (action_id, source_id, outcome, summary, evidence, verified_by, verified_at)
                   VALUES (%s, %s, 'success', %s, %s, 'prototype_fixture', now())
                   RETURNING id""",
                (action_id, source_id, result_summary,
                 Jsonb({"simulated": True, "recalled_claim_count": len(recalled)})),
            )
            result_id = cur.fetchone()["id"]
            cur.execute(
                """UPDATE secretary.task_steps
                   SET status = 'completed', checkpoint = %s
                   WHERE id = %s""",
                (Jsonb({"action_id": str(action_id), "result_id": str(result_id),
                        "simulated": True}), steps[2]["id"]),
            )

            # Do not claim an arbitrary natural-language criterion was met.
            # Only the fixed G1 fixture criterion is mechanically checked.
            criterion = task["completion_criteria"].lower()
            criteria_met = (("模擬診断" in criterion and "記録" in criterion)
                            or "record a simulated diagnosis" in criterion)
            final_status = "completed" if criteria_met else "waiting_external"
            if criteria_met:
                cur.execute(
                    """UPDATE secretary.task_steps
                       SET status = 'completed', checkpoint = %s
                       WHERE id = %s""",
                    (Jsonb({"criteria_met": True, "scope": "simulated_result_only"}),
                     steps[3]["id"]),
                )
            else:
                cur.execute(
                    """UPDATE secretary.task_steps
                       SET checkpoint = %s WHERE id = %s""",
                    (Jsonb({"criteria_met": False,
                            "reason": "requires human verification of free-text criteria"}),
                     steps[3]["id"]),
                )
            summary = {
                "task_id": str(task_id), "status": final_status,
                "phase": "recorded" if criteria_met else "needs_verification",
                "simulated": True, "criteria_met": criteria_met,
                "recalled_claims": recalled, "previous_results": previous_results,
                "action_id": str(action_id), "result_id": str(result_id),
                "result_summary": result_summary,
            }
            cur.execute(
                """UPDATE secretary.tasks
                   SET status = %s, completed_at = CASE WHEN %s THEN now() ELSE NULL END,
                       checkpoint = %s
                   WHERE id = %s""",
                (final_status, criteria_met, Jsonb({"g1": summary}), task_id),
            )
            cur.execute(
                """INSERT INTO secretary.audit_events
                   (actor, event_type, task_id, action_id, object_type, object_id, details)
                   VALUES (%s, 'prototype.mock_completed', %s, %s, 'task', %s, %s)""",
                (actor, task_id, action_id, task_id,
                 Jsonb({"simulated": True, "criteria_met": criteria_met})),
            )
            return summary



@app.get("/prototype/tasks/{task_id}")
def read_prototype_task(task_id: UUID, actor: str = Depends(authenticated)):
    """Retrieve the same Task ID plus persisted step statuses after restart."""
    with connect() as db:
        with db.cursor() as cur:
            cur.execute(
                """SELECT id, request, domain, completion_criteria, status,
                          revision, checkpoint, created_at, updated_at
                   FROM secretary.tasks
                   WHERE id = %s""",
                (task_id,),
            )
            task = cur.fetchone()
            if task is None:
                raise HTTPException(status_code=404, detail="Task not found.")
            cur.execute(
                """SELECT id, step_order, description, status, checkpoint,
                          attempt_count, last_error, updated_at
                   FROM secretary.task_steps
                   WHERE task_id = %s
                   ORDER BY step_order""",
                (task_id,),
            )
            steps = cur.fetchall()
    checkpoint = task.get("checkpoint") or {}
    phase = checkpoint.get("g1", {}).get("phase", "planned")
    return {**task, "steps": steps, "phase": phase}



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
