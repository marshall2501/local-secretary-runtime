"""Finance import/query contracts and deterministic content hashing."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Protocol

from .finance_preview import FinancePreview


SOURCE_SYSTEM = "moneyforward_me"


@dataclass(frozen=True)
class ImportPlan:
    source_sha256: str
    total: int
    inserted: int
    updated: int
    unchanged: int
    duplicate_external_ids: int


@dataclass(frozen=True)
class ImportResult:
    status: str
    batch_id: str | None
    source_sha256: str
    total: int
    inserted: int
    updated: int
    unchanged: int
    reason: str | None = None


@dataclass(frozen=True)
class FinanceDashboard:
    transaction_count: int
    calculation_target_count: int
    start_date: str | None
    end_date: str | None
    income_total: int
    expense_total: int
    net_total: int
    monthly: list[dict]
    categories: list[dict]
    recent_rows: list[dict]
    import_batches: list[dict]
    page: int
    page_size: int
    total_pages: int
    sort_by: str
    sort_dir: str
    row_mode: str


class FinanceRepository(Protocol):
    def load_finance_dashboard(self, **kwargs) -> FinanceDashboard: ...
    def finance_filter_options(self) -> dict[str, list[str]]: ...
    def plan_import(
        self,
        preview: FinancePreview,
        csv_bytes: bytes,
    ) -> ImportPlan: ...
    def commit_import(
        self,
        preview: FinancePreview,
        csv_bytes: bytes,
        filename: str,
    ) -> ImportResult: ...


def source_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def transaction_payload(row: dict) -> dict:
    return {
        "date": row["date"],
        "content": row["content"],
        "amount_jpy": int(row["amount"]),
        "account": row["account"],
        "major_category": row["major_category"],
        "minor_category": row["minor_category"],
        "memo": row["memo"],
        "is_transfer": bool(row["is_transfer"]),
        "calculation_target": bool(row["calculation_target"]),
        "external_id": row["external_id"],
    }


def transaction_content_hash(row: dict) -> str:
    raw = json.dumps(
        transaction_payload(row),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def load_finance_dashboard(
    repository: FinanceRepository,
    **kwargs,
) -> FinanceDashboard:
    return repository.load_finance_dashboard(**kwargs)


def finance_filter_options(
    repository: FinanceRepository,
) -> dict[str, list[str]]:
    return repository.finance_filter_options()


def plan_import(
    repository: FinanceRepository,
    preview: FinancePreview,
    csv_bytes: bytes,
) -> ImportPlan:
    return repository.plan_import(preview, csv_bytes)


def commit_import(
    repository: FinanceRepository,
    preview: FinancePreview,
    csv_bytes: bytes,
    filename: str,
) -> ImportResult:
    return repository.commit_import(preview, csv_bytes, filename)
