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

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from pydantic import BaseModel, Field, StringConstraints

from bootstrap.api_runtime import (
    MEMORY_KINDS,
    api_config,
    build_candidate_service,
    build_read_service,
    build_task_service,
    connect,
    verify_api_database,
    memory_search_sql,
)

from ritsuko.tasks.service import TaskConflictError, TaskNotFoundError


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


def read_service():
    return build_read_service(connect)


def task_service():
    return build_task_service(connect)


def candidate_service():
    return build_candidate_service(connect)


@asynccontextmanager
async def lifespan(_: FastAPI):
    verify_api_database(connect)
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
    return read_service().list_entities(domain=domain, limit=limit)


@app.get("/claims/current", dependencies=[Depends(read_authenticated)])
def current_claims(
    entity_id: UUID,
    verified_only: bool = False,
    limit: int = Query(default=100, ge=1, le=100),
):
    return read_service().list_current_claims(
        entity_id=entity_id, verified_only=verified_only, limit=limit
    )


@app.get("/tasks", dependencies=[Depends(read_authenticated)])
def tasks(
    task_status: TaskStatus | None = None,
    limit: int = Query(default=50, ge=1, le=100),
):
    return read_service().list_tasks(task_status=task_status, limit=limit)


@app.get("/memory/search", dependencies=[Depends(read_authenticated)])
def search_memory(
    q: str | None = Query(default=None, max_length=200),
    domain: str | None = Query(default=None, max_length=200),
    kind: Literal["claim", "issue", "hypothesis", "source"] | None = None,
    include_history: bool = False,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=1000000),
):
    return read_service().search_memory(
        q=q, domain=domain, kind=kind, include_history=include_history,
        limit=limit, offset=offset,
    )


@app.get("/experience/search", dependencies=[Depends(read_authenticated)])
def search_experience(
    domain: str | None = Query(default=None, max_length=200),
    entity_name: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=1000000),
):
    try:
        return read_service().search_experience(
            domain=domain, entity_name=entity_name, limit=limit, offset=offset
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/tasks", status_code=201)
def create_task(body: CreateTask, actor: str = Depends(authenticated)):
    return task_service().create_task(
        request=body.request,
        actor=actor,
        domain=body.domain,
        completion_criteria=body.completion_criteria,
        entity_id=body.entity_id,
        due_at=body.due_at,
    )


@app.post("/prototype/tasks", status_code=201)
def create_prototype_task(body: CreateTask, actor: str = Depends(authenticated)):
    return task_service().create_prototype_task(
        request=body.request,
        actor=actor,
        domain=body.domain,
        completion_criteria=body.completion_criteria,
        entity_id=body.entity_id,
        due_at=body.due_at,
    )


def _task_http_call(method, *args, **kwargs):
    try:
        return method(*args, **kwargs)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TaskConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/prototype/tasks/{task_id}/run")
def run_prototype_task(task_id: UUID, actor: str = Depends(authenticated)):
    return _task_http_call(task_service().run_prototype_task, task_id, actor)


@app.get("/prototype/tasks/{task_id}")
def read_prototype_task(task_id: UUID, actor: str = Depends(authenticated)):
    return _task_http_call(task_service().read_prototype_task, task_id)


def transition_task(
    task_id: UUID,
    revision: int,
    expected_statuses: tuple[str, ...],
    destination: str,
    actor: str,
):
    return _task_http_call(
        task_service().transition_task,
        task_id,
        revision,
        expected_statuses,
        destination,
        actor,
    )


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
    return transition_task(
        task_id, body.expected_revision, ("paused",), "pending", actor
    )


@app.post("/pending-claims", status_code=201)
def propose_claim(body: NewCandidate, actor: str = Depends(authenticated)):
    return candidate_service().propose(
        actor=actor,
        entity_id=body.entity_id,
        source_id=body.source_id,
        claim_type=body.claim_type,
        predicate=body.predicate,
        proposed_value=body.proposed_value,
        confidence=body.confidence,
        evidence=body.evidence,
        extraction_model=body.extraction_model,
        prompt_version=body.prompt_version,
    )
