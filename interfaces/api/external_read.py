# Provenance-Origin: original
# Provenance-AI: AI-generated
"""Legacy read-client URL adapter backed by Secretary's shared ReadService.

The separate 8010 Task executor is NOT imported or mounted. Existing MCP and
PVA readers can migrate their URLs to this one Secretary Server process.
"""
from __future__ import annotations

import hmac
from typing import Callable, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query


def create_external_read_router(*, read_service=None,
                                token_loader: Callable[[], str | None] | None = None
                                ) -> APIRouter:
    """Serve the read-only external REST contracts from the shared application.

    Args:
        read_service: Existing transport-independent ReadService.
        token_loader: Optional existing external read token provider; never logs it.

    Returns:
        APIRouter: Compatibility URLs used by PVA/MCP on the unified Host.
    """
    router = APIRouter(tags=["external-read"])

    def require_reader(authorization: str | None = Header(default=None)):
        if token_loader is None or read_service is None:
            raise HTTPException(503, "external_read_not_configured")
        token = token_loader()
        if not token:
            raise HTTPException(503, "external_read_not_configured")
        if not hmac.compare_digest(authorization or "", "Bearer " + token):
            raise HTTPException(401, "invalid_read_token")

    guarded = [Depends(require_reader)]

    @router.get("/entities", dependencies=guarded)
    def entities(domain: str | None = Query(default=None, max_length=200),
                 limit: int = Query(default=50, ge=1, le=100)):
        return read_service.list_entities(domain=domain, limit=limit)

    @router.get("/claims/current", dependencies=guarded)
    def current_claims(entity_id: UUID, verified_only: bool = False,
                       limit: int = Query(default=100, ge=1, le=100)):
        return read_service.list_current_claims(
            entity_id=entity_id, verified_only=verified_only, limit=limit,
        )

    @router.get("/tasks", dependencies=guarded)
    def tasks(task_status: str | None = None, status: str | None = None,
              limit: int = Query(default=50, ge=1, le=100)):
        if task_status is not None and status is not None and task_status != status:
            raise HTTPException(422, "Conflicting task status filters.")
        return read_service.list_tasks(
            task_status=task_status if task_status is not None else status,
            limit=limit,
        )

    @router.get("/memory/search", dependencies=guarded)
    def memory_search(
        q: str | None = Query(default=None, max_length=200),
        domain: str | None = Query(default=None, max_length=200),
        kind: Literal["claim", "issue", "hypothesis", "source"] | None = None,
        include_history: bool = False,
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=1000000),
    ):
        return read_service.search_memory(
            q=q, domain=domain, kind=kind, include_history=include_history,
            limit=limit, offset=offset,
        )

    @router.get("/experience/search", dependencies=guarded)
    def experience_search(
        domain: str | None = Query(default=None, max_length=200),
        entity_name: str | None = Query(default=None, max_length=200),
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=1000000),
    ):
        try:
            return read_service.search_experience(
                domain=domain, entity_name=entity_name,
                limit=limit, offset=offset,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    return router
