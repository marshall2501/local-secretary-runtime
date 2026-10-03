"""Read-only MoneyForward ME CSV preview helpers.

This module never writes financial data to PostgreSQL and never sends it to an LLM.
It is intentionally dependency-free so the first finance slice can validate the
export structure before designing/importing the real financial schema.
"""
from __future__ import annotations

import csv
import io
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

EXPECTED_COLUMNS = (
    "計算対象",
    "日付",
    "内容",
    "金額（円）",
    "保有金融機関",
    "大項目",
    "中項目",
    "メモ",
    "振替",
    "ID",
)


@dataclass(frozen=True)
class FinancePreview:
    filename: str
    row_count: int
    start_date: str
    end_date: str
    calculation_target_count: int
    transfer_count: int
    unique_id_count: int
    duplicate_id_count: int
    income_total: int
    expense_total: int
    net_total: int
    monthly: list[dict]
    categories: list[dict]
    accounts: list[dict]
    recent_rows: list[dict]
    transactions: list[dict]


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp932"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("CSVをUTF-8/CP932として読み取れません")


def analyze_moneyforward_csv(data: bytes, filename: str = "moneyforward.csv") -> FinancePreview:
    if not data:
        raise ValueError("CSVが空です")

    text = _decode(data)
    reader = csv.DictReader(io.StringIO(text))
    columns = tuple(reader.fieldnames or ())
    if columns != EXPECTED_COLUMNS:
        raise ValueError(
            "MoneyForward想定列と一致しません: " + " / ".join(columns)
        )

    rows = list(reader)
    if not rows:
        raise ValueError("明細行がありません")

    parsed = []
    ids: list[str] = []
    monthly = defaultdict(lambda: {"income": 0, "expense": 0, "net": 0, "count": 0})
    categories = Counter()
    accounts = Counter()

    for index, row in enumerate(rows, start=2):
        try:
            date = datetime.strptime(row["日付"].strip(), "%Y/%m/%d").date()
        except ValueError as exc:
            raise ValueError(f"{index}行目の日付が不正です") from exc
        try:
            amount = int(Decimal(row["金額（円）"].replace(",", "").strip()))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"{index}行目の金額が不正です") from exc

        calculation_target = row["計算対象"].strip() == "1"
        transfer = row["振替"].strip() == "1"
        external_id = row["ID"].strip()
        if external_id:
            ids.append(external_id)

        item = {
            "date": date.isoformat(),
            "content": row["内容"].strip(),
            "amount": amount,
            "account": row["保有金融機関"].strip(),
            "major_category": row["大項目"].strip(),
            "minor_category": row["中項目"].strip(),
            "memo": row["メモ"].strip(),
            "is_transfer": transfer,
            "calculation_target": calculation_target,
            "external_id": external_id,
        }
        parsed.append(item)

        if calculation_target:
            month = date.strftime("%Y-%m")
            bucket = monthly[month]
            bucket["count"] += 1
            bucket["net"] += amount
            if amount >= 0:
                bucket["income"] += amount
            else:
                bucket["expense"] += -amount
            categories[(item["major_category"], item["minor_category"])] += -amount if amount < 0 else 0
            accounts[item["account"]] += 1

    target_rows = [r for r in parsed if r["calculation_target"]]
    income_total = sum(r["amount"] for r in target_rows if r["amount"] > 0)
    expense_total = sum(-r["amount"] for r in target_rows if r["amount"] < 0)

    duplicate_count = len(ids) - len(set(ids))
    category_rows = [
        {"major": major, "minor": minor, "expense": expense}
        for (major, minor), expense in categories.most_common(15)
        if expense > 0
    ]
    account_rows = [
        {"account": account, "count": count}
        for account, count in accounts.most_common()
    ]
    monthly_rows = [
        {
            "month": month,
            "income": values["income"],
            "expense": values["expense"],
            "net": values["net"],
            "count": values["count"],
        }
        for month, values in sorted(monthly.items(), reverse=True)
    ]

    dates = [datetime.fromisoformat(r["date"]).date() for r in parsed]
    recent = sorted(parsed, key=lambda r: (r["date"], r["external_id"]), reverse=True)[:100]

    return FinancePreview(
        filename=filename,
        row_count=len(parsed),
        start_date=min(dates).isoformat(),
        end_date=max(dates).isoformat(),
        calculation_target_count=len(target_rows),
        transfer_count=sum(1 for r in parsed if r["is_transfer"]),
        unique_id_count=len(set(ids)),
        duplicate_id_count=duplicate_count,
        income_total=income_total,
        expense_total=expense_total,
        net_total=income_total - expense_total,
        monthly=monthly_rows,
        categories=category_rows,
        accounts=account_rows,
        recent_rows=recent,
        transactions=parsed,
    )
