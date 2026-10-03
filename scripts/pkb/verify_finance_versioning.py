"""Rollback-only integration probe for finance version history.

The probe uses a clearly fictional MoneyForward external ID inside an outer
transaction, exercises the real commit_import path twice with changed content,
verifies current + version history, and deliberately rolls back everything.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import psycopg

from capabilities.finance.finance_import import (
    EXPECTED_DB,
    EXPECTED_USER,
    SOURCE_SYSTEM,
    commit_import,
)
from capabilities.finance.finance_preview import analyze_moneyforward_csv


PROBE_EXTERNAL_ID = "__lsa_finance_version_probe__"

_HEADER = "計算対象,日付,内容,金額（円）,保有金融機関,大項目,中項目,メモ,振替,ID\n"
_FIRST = (
    _HEADER
    + "1,2026/09/28,架空テスト支出,-1234,架空カード,食費,その他,初回,0,"
    + PROBE_EXTERNAL_ID
    + "\n"
).encode("utf-8-sig")
_CHANGED = (
    _HEADER
    + "1,2026/09/28,架空テスト支出,-1234,架空カード,日用品,消耗品,カテゴリ変更,0,"
    + PROBE_EXTERNAL_ID
    + "\n"
).encode("utf-8-sig")


@dataclass(frozen=True)
class ProbeResult:
    first_status: str
    second_status: str
    first_inserted: int
    second_updated: int
    current_major: str
    current_minor: str
    version_count: int
    batch_count: int
    rolled_back: bool


class _RollbackProbe(Exception):
    def __init__(self, result: ProbeResult):
        super().__init__("rollback finance version probe")
        self.result = result


def verify_version_history_rollback(db) -> ProbeResult:
    """Run the real import path, verify versioning, and leave zero persisted rows."""
    first = analyze_moneyforward_csv(_FIRST, "finance_probe_first.csv")
    changed = analyze_moneyforward_csv(_CHANGED, "finance_probe_changed.csv")

    try:
        with db.transaction():
            with db.cursor() as cur:
                cur.execute(
                    """SELECT count(*) FROM secretary.finance_transactions
                       WHERE source_system=%s AND external_id=%s""",
                    (SOURCE_SYSTEM, PROBE_EXTERNAL_ID),
                )
                if cur.fetchone()[0] != 0:
                    raise ValueError("finance version probe external ID already exists")

            first_result = commit_import(db, first, _FIRST, "finance_probe_first.csv")
            second_result = commit_import(db, changed, _CHANGED, "finance_probe_changed.csv")

            with db.cursor() as cur:
                cur.execute(
                    """SELECT c.major_name, c.minor_name
                       FROM secretary.finance_transactions t
                       LEFT JOIN secretary.finance_categories c ON c.id=t.category_id
                       WHERE t.source_system=%s AND t.external_id=%s""",
                    (SOURCE_SYSTEM, PROBE_EXTERNAL_ID),
                )
                current = cur.fetchone()
                if current is None:
                    raise AssertionError("probe transaction missing after import")

                cur.execute(
                    """SELECT count(*)
                       FROM secretary.finance_transaction_versions
                       WHERE source_system=%s AND external_id=%s""",
                    (SOURCE_SYSTEM, PROBE_EXTERNAL_ID),
                )
                version_count = int(cur.fetchone()[0])

                cur.execute(
                    """SELECT count(*)
                       FROM secretary.finance_import_batches
                       WHERE source_system=%s
                         AND source_filename IN ('finance_probe_first.csv','finance_probe_changed.csv')"""
                    ,
                    (SOURCE_SYSTEM,),
                )
                batch_count = int(cur.fetchone()[0])

            result = ProbeResult(
                first_status=first_result.status,
                second_status=second_result.status,
                first_inserted=first_result.inserted,
                second_updated=second_result.updated,
                current_major=current[0] or "",
                current_minor=current[1] or "",
                version_count=version_count,
                batch_count=batch_count,
                rolled_back=False,
            )
            if (
                result.first_status != "committed"
                or result.second_status != "committed"
                or result.first_inserted != 1
                or result.second_updated != 1
                or result.current_major != "日用品"
                or result.current_minor != "消耗品"
                or result.version_count != 2
                or result.batch_count != 2
            ):
                raise AssertionError("finance version probe verification failed")

            raise _RollbackProbe(result)
    except _RollbackProbe as exc:
        result = exc.result

    with db.cursor() as cur:
        cur.execute(
            """SELECT
                 (SELECT count(*) FROM secretary.finance_transactions
                  WHERE source_system=%s AND external_id=%s),
                 (SELECT count(*) FROM secretary.finance_transaction_versions
                  WHERE source_system=%s AND external_id=%s),
                 (SELECT count(*) FROM secretary.finance_import_batches
                  WHERE source_system=%s
                    AND source_filename IN ('finance_probe_first.csv','finance_probe_changed.csv'))""",
            (
                SOURCE_SYSTEM, PROBE_EXTERNAL_ID,
                SOURCE_SYSTEM, PROBE_EXTERNAL_ID,
                SOURCE_SYSTEM,
            ),
        )
        transaction_count, version_count_after, batch_count_after = cur.fetchone()

    if any(int(v) != 0 for v in (transaction_count, version_count_after, batch_count_after)):
        raise AssertionError("finance version probe rollback verification failed")

    return ProbeResult(
        first_status=result.first_status,
        second_status=result.second_status,
        first_inserted=result.first_inserted,
        second_updated=result.second_updated,
        current_major=result.current_major,
        current_minor=result.current_minor,
        version_count=result.version_count,
        batch_count=result.batch_count,
        rolled_back=True,
    )


def _port() -> int:
    raw = os.environ.get("LSA_PKB_DAILY_PORT", "")
    value = int(raw)
    if not 1024 <= value <= 65535:
        raise RuntimeError("invalid prototype DB port")
    return value


def _secret() -> str:
    raw = os.environ.get("LSA_PKB_DAILY_SECRET", "")
    path = Path(raw)
    if not raw or not path.is_file():
        raise RuntimeError("missing prototype writer secret")
    password = path.read_text(encoding="utf-8").strip()
    if len(password) < 32:
        raise RuntimeError("invalid prototype writer secret")
    return password


def main() -> None:
    with psycopg.connect(
        dbname=EXPECTED_DB,
        host="127.0.0.1",
        port=_port(),
        user=EXPECTED_USER,
        password=_secret(),
        connect_timeout=5,
        autocommit=True,
    ) as db:
        result = verify_version_history_rollback(db)
    print(
        "PASS: finance version history probe "
        f"insert={result.first_inserted} update={result.second_updated} "
        f"versions={result.version_count} current={result.current_major}/{result.current_minor} "
        f"batches={result.batch_count} rollback={str(result.rolled_back).lower()}"
    )


if __name__ == "__main__":
    main()
