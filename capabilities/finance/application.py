"""Finance capability application queries shared by GUI and RITSUKO."""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from .finance_import import load_finance_dashboard


def core_finance_filters(text: str) -> dict:
    q = text.strip()
    start_date = None
    end_date = None
    explicit = re.search(r"(?P<year>20\d{2})年(?P<month>1[0-2]|0?[1-9])月", q)
    if explicit:
        year = int(explicit.group("year"))
        month = int(explicit.group("month"))
        start = datetime(year, month, 1).date()
        next_month = (
            datetime(year + 1, 1, 1).date()
            if month == 12 else datetime(year, month + 1, 1).date()
        )
        start_date = start.isoformat()
        end_date = (next_month - timedelta(days=1)).isoformat()
    elif "今月" in q:
        today = datetime.now().date()
        start = today.replace(day=1)
        next_month = (
            today.replace(year=today.year + 1, month=1, day=1)
            if today.month == 12 else today.replace(month=today.month + 1, day=1)
        )
        start_date = start.isoformat()
        end_date = (next_month - timedelta(days=1)).isoformat()
    return {"start_date": start_date, "end_date": end_date, "row_mode": "calculation_target"}


def query_finance_text(text: str, *, repository) -> dict:
    filters = core_finance_filters(text)
    dashboard = load_finance_dashboard(
        repository,
        recent_limit=5,
        start_date=filters["start_date"],
        end_date=filters["end_date"],
        row_mode=filters["row_mode"],
        page=1,
        sort_by="date",
        sort_dir="desc",
    )
    return {
        "status": "ok", "result_kind": "finance_summary",
        "total": dashboard.transaction_count,
        "transaction_count": dashboard.transaction_count,
        "calculation_target_count": dashboard.calculation_target_count,
        "requested_start_date": filters["start_date"],
        "requested_end_date": filters["end_date"],
        "data_start_date": dashboard.start_date, "data_end_date": dashboard.end_date,
        "income_total": dashboard.income_total, "expense_total": dashboard.expense_total,
        "net_total": dashboard.net_total, "monthly": dashboard.monthly[:12],
        "categories": dashboard.categories[:10], "recent_rows": dashboard.recent_rows[:5],
        "import_batches": dashboard.import_batches[:1],
    }


def finance_core_answer(result: dict) -> str:
    period = ""
    if result.get("requested_start_date") or result.get("requested_end_date"):
        period = f"{result.get('requested_start_date') or '-'}〜{result.get('requested_end_date') or '-'}の"
    if int(result.get("total") or 0) == 0:
        return (
            "保存済み家計では、"
            + period
            + "集計対象となる明細が見つかりませんでした。"
        )
    return (
        f"保存済み家計では、{period}集計対象は{result['transaction_count']}件、"
        f"収入は¥{int(result['income_total']):,}、"
        f"支出は¥{int(result['expense_total']):,}、"
        f"収支は¥{int(result['net_total']):,}です。"
    )
