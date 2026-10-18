# Provenance-Origin: original
# Provenance-AI: AI-generated
"""Independent PKB HTTP commands reusing the existing daily Application.

PKB write, correction and Pending decisions do not pass through RITSUKO.
This router owns only validation and HTTP error conversion.
"""
from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from pkb.application.daily import correct_text, register_text


class PkbTextCommand(BaseModel):
    """User-provided original text for memory registration or correction."""

    text: str = Field(min_length=1, max_length=10000)


class PendingDecision(BaseModel):
    """User-confirmed outcome for an existing PKB Pending claim."""

    decision: Literal["rejected", "needs_edit"]


def create_pkb_command_router(*, repository=None, interpreter=None) -> APIRouter:
    """Build PKB write endpoints without introducing a second business executor.

    Args:
        repository: Existing PKB persistence port.
        interpreter: Existing daily PKB interpreter for ambiguous statements.

    Returns:
        APIRouter: Shared HTTP adapter for PKB operations.
    """
    router = APIRouter(prefix="/api/v1/pkb", tags=["pkb"])

    def repo():
        if repository is None:
            raise HTTPException(503, "pkb_repository_not_configured")
        return repository

    @router.post("/register")
    async def register(body: PkbTextCommand):
        """Register an explicit claim or return a Pending review candidate."""
        if interpreter is None:
            raise HTTPException(503, "pkb_interpreter_not_configured")
        return await asyncio.to_thread(
            register_text, body.text, repository=repo(), interpreter=interpreter,
        )

    @router.post("/correct")
    async def correct(body: PkbTextCommand):
        """Apply an eligible correction using the existing PKB application."""
        return await asyncio.to_thread(correct_text, body.text, repository=repo())

    @router.get("/pending")
    async def pending(limit: int = Query(default=50, ge=1, le=100)):
        """List unreviewed PKB claims directly from the PKB capability."""
        return await asyncio.to_thread(repo().list_pending, limit)

    @router.get("/reviewed")
    async def reviewed(limit: int = Query(default=20, ge=1, le=100)):
        """List recorded user review decisions for display and audit."""
        return await asyncio.to_thread(repo().list_reviewed, limit)

    @router.post("/pending/{pending_id}/accept")
    async def accept(pending_id: str):
        """Accept only a claim that the PKB application marks eligible."""
        return await asyncio.to_thread(repo().accept_pending, pending_id)

    @router.post("/pending/{pending_id}/review")
    async def review(pending_id: str, body: PendingDecision):
        """Record a rejected/needs_edit decision for a Pending claim."""
        return await asyncio.to_thread(
            repo().review_pending, pending_id, body.decision,
        )

    return router
