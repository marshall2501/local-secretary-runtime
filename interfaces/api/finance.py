# Provenance-Origin: original
# Provenance-AI: AI-generated
"""Finance read API calling the shared Finance Application and repository.

The router does not read files, parse MoneyForward CSV, or own SQL.
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from capabilities.finance.finance_import import (
    finance_filter_options,
    load_finance_dashboard,
    plan_import, commit_import,
    source_sha256,
)
from capabilities.finance.finance_preview import analyze_moneyforward_csv


class FinanceCsvPayload(BaseModel):
    """Uploaded MoneyForward CSV as Base64; no temporary upload files."""

    filename: str = Field(min_length=1, max_length=200)
    csv_base64: str = Field(min_length=1, max_length=32_000_000)
    expected_sha256: str | None = Field(default=None, min_length=64, max_length=64)


def _parse_upload(payload: FinanceCsvPayload):
    """Parse the same MoneyForward source used by legacy Finance Application."""
    try:
        raw = base64.b64decode(payload.csv_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(422, "invalid_csv_base64") from exc
    if len(raw) > 20 * 1024 * 1024:
        raise HTTPException(413, "csv_too_large")
    if payload.expected_sha256 is not None:
        if source_sha256(raw) != payload.expected_sha256.lower():
            raise HTTPException(409, "csv_changed_since_preview")
    try:
        return raw, analyze_moneyforward_csv(raw, filename=payload.filename)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


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

    @router.post("/import/plan")
    def import_plan(payload: FinanceCsvPayload):
        """Preview CSV rows and existing-db insert/update counts; do not write."""
        raw, preview = _parse_upload(payload)
        plan = plan_import(require_repo(), preview, raw)
        summary = {key: value for key, value in asdict(preview).items()
                   if key != "transactions"}
        return {"preview": summary, "plan": asdict(plan)}

    @router.post("/import/commit")
    def import_commit(payload: FinanceCsvPayload):
        """Explicitly commit only the source confirmed by its preview digest."""
        if payload.expected_sha256 is None:
            raise HTTPException(422, "preview_hash_required")
        raw, preview = _parse_upload(payload)
        result = commit_import(require_repo(), preview, raw, payload.filename)
        return asdict(result)

    return router
