# Provenance-Origin: original
# Provenance-AI: AI-generated
"""Magi profile and member assignment REST adapter for one Secretary Server."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ritsuko.magi.settings import (
    list_llm_profiles, load_member_specs,
    save_member_assignments, upsert_llm_profile,
)


class LlmProfileBody(BaseModel):
    """Existing connection/model configuration, independent of MAGI slots."""

    profile_id: str | None = None
    connection_id: str
    model: str = Field(min_length=1, max_length=200)
    display_name: str | None = None
    context_window_tokens: int | None = Field(default=None, ge=512)
    ollama_num_predict: int | None = Field(default=None, ge=1)
    enabled: bool = True


class MemberAssignmentBody(BaseModel):
    """One of the three logical MAGI slots."""

    name: str
    profile_id: str
    weight: float = Field(default=1.0, ge=0.1, le=10)
    timeout_seconds: int = Field(default=120, ge=10, le=1800)
    retry_within_turn: bool = False
    enabled: bool = True


class AssignmentsBody(BaseModel):
    """Complete three-member assignment update, validated by MAGI Core."""

    assignments: list[MemberAssignmentBody] = Field(min_length=3, max_length=3)


def create_magi_settings_router(*, repository=None) -> APIRouter:
    """Expose existing MAGI configuration without a second provider engine.

    Args:
        repository: Existing transport-independent settings repository.

    Returns:
        APIRouter: Profile/member management consumed by Vue and REST.
    """
    router = APIRouter(prefix="/api/v1/magi", tags=["magi"])

    def repo():
        if repository is None:
            raise HTTPException(503, "magi_settings_unavailable")
        return repository

    @router.get("/profiles")
    def profiles(include_disabled: bool = True):
        return list_llm_profiles(repo(), include_disabled=include_disabled)

    @router.post("/profiles")
    def upsert_profile(body: LlmProfileBody):
        try:
            return upsert_llm_profile(repo(), **body.model_dump(exclude_none=True))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/members")
    def members():
        return load_member_specs(repo())

    @router.post("/members")
    def write_members(body: AssignmentsBody):
        try:
            return save_member_assignments(
                repo(), [x.model_dump() for x in body.assignments],
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    return router
