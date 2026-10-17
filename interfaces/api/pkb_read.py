# Provenance-Origin: original
# Provenance-AI: AI-generated
"""Reusable read-only PKB search router for local daily UI hosts.

The transport adapter depends only on the existing ReadService contract.
It never opens a database, writes memory or decides Task completion.
Hosts must enforce their own authentication or localhost/origin boundary.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from application.read_service import ReadService


def create_pkb_read_router(*, read_service: ReadService | None = None) -> APIRouter:
    """Mount SQL-first PKB memory search through an injected application service.

    Args:
        read_service: Existing common read application, or None to fail closed.

    Returns:
        APIRouter: Bounded, read-only PKB endpoints with no storage dependencies.
    """
    router = APIRouter()

    @router.get("/api/v1/pkb/memory/search")
    def memory_search(
        q: str | None = Query(default=None, max_length=200),
        domain: str | None = Query(default=None, max_length=200),
        kind: Literal["claim", "issue", "hypothesis", "source"] | None = None,
        include_history: bool = False,
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=1000000),
    ) -> dict:
        """Query existing Source/Claim records without any write operations."""
        if read_service is None:
            raise HTTPException(503, "pkb_read_not_configured")
        try:
            return read_service.search_memory(
                q=q, domain=domain, kind=kind,
                include_history=include_history, limit=limit, offset=offset,
            )
        except (RuntimeError, OSError):
            raise HTTPException(503, "pkb_read_unavailable") from None

    return router
