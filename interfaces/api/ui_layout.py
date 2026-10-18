# Provenance-Origin: original
# Provenance-AI: AI-generated
"""Read/write-only transport for layout defaults shared with the legacy UI.

The API never reaches Task state, PKB, finance data, or Secret storage.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from interfaces.vue_daily.layout_preferences import (
    default_layout_preferences, read_layout_preferences, save_layout_preferences,
)


class LayoutPreferencesBody(BaseModel):
    """Known top-level display groups, validated in the preference service."""

    model_config = ConfigDict(extra="forbid")

    pkb: dict
    finance: dict
    core: dict
    entity: dict
    visibility: dict
    debug: dict


def create_ui_layout_router(*, allow_changes: bool) -> APIRouter:
    """Expose layout settings only; writes require configured user commands."""
    router = APIRouter(prefix="/api/v1/ui", tags=["ui"])

    @router.get("/layout")
    def get_layout() -> dict:
        """Read normalized defaults without changing saved settings."""
        try:
            return read_layout_preferences()
        except (OSError, UnicodeDecodeError, ValueError):
            raise HTTPException(503, "ui_preferences_unavailable") from None

    @router.get("/layout/defaults")
    def get_layout_defaults() -> dict:
        """Expose original defaults for a preview; GET does not reset any file."""
        return default_layout_preferences()

    @router.post("/layout")
    def update_layout(body: LayoutPreferencesBody) -> dict:
        """Explicitly save a fully validated layout projection."""
        if not allow_changes:
            raise HTTPException(503, "commands_not_configured")
        try:
            return save_layout_preferences(body.model_dump())
        except ValueError:
            raise HTTPException(422, "invalid_ui_layout") from None
        except (OSError, UnicodeDecodeError):
            raise HTTPException(503, "ui_preferences_unavailable") from None

    return router
