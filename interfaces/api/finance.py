# Provenance-Origin: original
# Provenance-AI: AI-generated
"""Finance read API calling the shared Finance Application and repository.

The router does not read files, parse MoneyForward CSV, or own SQL.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from capabilities.finance.finance_import import (
    finance_filter_options,
    load_finance_dashboard,
)


def create_finance_router(*, repository=None) -> APIRouter:
    """Expose stored finance dashboard and selectable filters through one Host.

    Args:
        repository: Existing Finance persistence port, wired by bootstrap.

    Returns:
        APIRouter: REST adapter for the Finance Application.
    """
    router = APIRouter(prefix="/api/v1/finance", tags=["finance"])

    def require_repo():
        if repository is None:
            raise HTTPException(status_code=503, detail="finance_unavailable")
        return repository

    @router.get("/dashboard")
    def dashboard(
        recent_limit: int = Query(default=25, ge=1, le=200),
        start_date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
        end_date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
        account: str | None = Query(default=None, max_length=200),
        major_category: str | None = Query(default=None, max_length=200),
        search_text: str | None = Query(default=None, max_length=200),
        row_mode: Literal["calculation_target", "transfer", "all"] = "calculation_target",
        page: int = Query(default=1, ge=1, le=100000),
        sort_by: Literal["date", "amount"] = "date",
        sort_dir: Literal["asc", "desc"] = "desc",
    ):
        """Read the same persisted dashboard that the old NiceGUI used."""
        return load_finance_dashboard(
            require_repo(), recent_limit=recent_limit, start_date=start_date,
            end_date=end_date, account=account, major_category=major_category,
            search_text=search_text, row_mode=row_mode, page=page,
            sort_by=sort_by, sort_dir=sort_dir,
        )

    @router.get("/options")
    def filter_options():
        """Read account and category choices from the same repository."""
        return finance_filter_options(require_repo())

    return router
