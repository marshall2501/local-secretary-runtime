"""PostgreSQL adapter for normalized MoneyForward finance persistence."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

from capabilities.finance.finance_import import (
    FinanceDashboard,
    ImportPlan,
    ImportResult,
    SOURCE_SYSTEM,
    source_sha256,
    transaction_content_hash,
    transaction_payload,
)
from capabilities.finance.finance_preview import FinancePreview
from config.runtime_database import LOCAL_HOSTS, allowed_daily_connection

EXPECTED_DB = "secretary_pkb_proto_20260927"
EXPECTED_USER = "secretary_pkb_proto_writer_20260927"


def _finance_filter_clause(
    start_date: str | None = None,
    end_date: str | None = None,
    account: str | None = None,
    major_category: str | None = None,
    search_text: str | None = None,
    row_mode: str = "calculation_target",
) -> tuple[str, list]:
    clauses = ["t.source_system=%s"]
    params: list = [SOURCE_SYSTEM]

    if start_date:
        clauses.append("t.transaction_date >= %s::date")
        params.append(start_date)
    if end_date:
        clauses.append("t.transaction_date <= %s::date")
        params.append(end_date)
    if account:
        clauses.append("a.external_name = %s")
        params.append(account)
    if major_category:
        clauses.append("c.major_name = %s")
        params.append(major_category)
    if search_text:
        clauses.append(
            "(t.description ILIKE %s OR t.memo ILIKE %s OR "
            "COALESCE(a.external_name,'') ILIKE %s OR "
            "COALESCE(c.major_name,'') ILIKE %s OR "
            "COALESCE(c.minor_name,'') ILIKE %s)"
        )
        pattern = "%" + search_text.strip() + "%"
        params.extend([pattern, pattern, pattern, pattern, pattern])

    if row_mode == "calculation_target":
        clauses.append("t.calculation_target")
    elif row_mode == "transfer":
        clauses.append("t.is_transfer")
    elif row_mode != "all":
        raise ValueError("unsupported finance row_mode")

    return " AND ".join(clauses), params


def _guard_db(db) -> None:
    info = db.info
    if (info.host or "") not in LOCAL_HOSTS:
        raise ValueError("Refusing non-local finance database")
    if allowed_daily_connection(db):
        return
    if (info.dbname or "") != EXPECTED_DB:
        raise ValueError("Refusing non-prototype finance database")
    raise ValueError("Refusing non-dedicated finance writer")


def load_finance_dashboard(
    db,
    recent_limit: int = 25,
    start_date: str | None = None,
    end_date: str | None = None,
    account: str | None = None,
    major_category: str | None = None,
    search_text: str | None = None,
    row_mode: str = "calculation_target",
    page: int = 1,
    sort_by: str = "date",
    sort_dir: str = "desc",
) -> FinanceDashboard:
    _guard_db(db)
    if recent_limit < 1 or recent_limit > 200:
        raise ValueError("recent_limit must be between 1 and 200")
    if page < 1:
        raise ValueError("page must be >= 1")
    if sort_by not in {"date", "amount"}:
        raise ValueError("unsupported finance sort_by")
    if sort_dir not in {"asc", "desc"}:
        raise ValueError("unsupported finance sort_dir")

    where_sql, params = _finance_filter_clause(
        start_date=start_date,
        end_date=end_date,
        account=account,
        major_category=major_category,
        search_text=search_text,
        row_mode=row_mode,
    )

    with db.cursor() as cur:
        cur.execute(
            f"""SELECT
                   count(*),
                   count(*) FILTER (WHERE t.calculation_target),
                   min(t.transaction_date),
                   max(t.transaction_date),
                   COALESCE(sum(t.amount_jpy) FILTER (
                     WHERE t.amount_jpy > 0
                   ), 0),
                   COALESCE(sum(-t.amount_jpy) FILTER (
                     WHERE t.amount_jpy < 0
                   ), 0)
               FROM secretary.finance_transactions t
               LEFT JOIN secretary.finance_accounts a ON a.id=t.account_id
               LEFT JOIN secretary.finance_categories c ON c.id=t.category_id
               WHERE {where_sql}""",
            tuple(params),
        )
        (
            count,
            calculation_target_count,
            actual_start_date,
            actual_end_date,
            income_total,
            expense_total,
        ) = cur.fetchone()

        cur.execute(
            f"""SELECT
                   to_char(t.transaction_date, 'YYYY-MM') AS month,
                   COALESCE(sum(t.amount_jpy) FILTER (WHERE t.amount_jpy > 0), 0),
                   COALESCE(sum(-t.amount_jpy) FILTER (WHERE t.amount_jpy < 0), 0),
                   COALESCE(sum(t.amount_jpy), 0),
                   count(*)
               FROM secretary.finance_transactions t
               LEFT JOIN secretary.finance_accounts a ON a.id=t.account_id
               LEFT JOIN secretary.finance_categories c ON c.id=t.category_id
               WHERE {where_sql}
               GROUP BY 1
               ORDER BY 1 DESC""",
            tuple(params),
        )
        monthly = [
            {
                "month": row[0],
                "income": int(row[1]),
                "expense": int(row[2]),
                "net": int(row[3]),
                "count": int(row[4]),
            }
            for row in cur.fetchall()
        ]

        cur.execute(
            f"""SELECT
                   COALESCE(c.major_name, ''),
                   COALESCE(c.minor_name, ''),
                   sum(-t.amount_jpy)
               FROM secretary.finance_transactions t
               LEFT JOIN secretary.finance_accounts a ON a.id=t.account_id
               LEFT JOIN secretary.finance_categories c ON c.id=t.category_id
               WHERE {where_sql}
                 AND t.calculation_target
                 AND t.amount_jpy < 0
               GROUP BY c.major_name, c.minor_name
               ORDER BY 3 DESC, 1, 2
               LIMIT 50""",
            tuple(params),
        )
        categories = [
            {"major": row[0], "minor": row[1], "expense": int(row[2])}
            for row in cur.fetchall()
        ]

        total_pages = max(1, (int(count) + recent_limit - 1) // recent_limit)
        effective_page = min(page, total_pages)
        offset = (effective_page - 1) * recent_limit
        order_column = (
            "t.transaction_date" if sort_by == "date" else "t.amount_jpy"
        )
        order_direction = "ASC" if sort_dir == "asc" else "DESC"

        cur.execute(
            f"""SELECT
                   t.external_id,
                   t.transaction_date,
                   t.description,
                   t.amount_jpy,
                   COALESCE(a.external_name, ''),
                   COALESCE(c.major_name, ''),
                   COALESCE(c.minor_name, ''),
                   t.memo,
                   t.is_transfer,
                   t.calculation_target,
                   t.last_seen_at
               FROM secretary.finance_transactions t
               LEFT JOIN secretary.finance_accounts a ON a.id=t.account_id
               LEFT JOIN secretary.finance_categories c ON c.id=t.category_id
               WHERE {where_sql}
               ORDER BY {order_column} {order_direction},
                        t.external_id {order_direction}
               LIMIT %s OFFSET %s""",
            tuple(params + [recent_limit, offset]),
        )
        recent_rows = [
            {
                "external_id": row[0],
                "date": row[1].isoformat(),
                "content": row[2],
                "amount": int(row[3]),
                "account": row[4],
                "major_category": row[5],
                "minor_category": row[6],
                "memo": row[7],
                "is_transfer": bool(row[8]),
                "calculation_target": bool(row[9]),
                "last_seen_at": row[10].isoformat(),
            }
            for row in cur.fetchall()
        ]

        cur.execute(
            """SELECT id, source_filename, source_sha256, row_count,
                      imported_at, status
               FROM secretary.finance_import_batches
               WHERE source_system=%s
               ORDER BY imported_at DESC""",
            (SOURCE_SYSTEM,),
        )
        import_batches = [
            {
                "id": str(row[0]),
                "source_filename": row[1],
                "source_sha256": row[2],
                "row_count": int(row[3]),
                "imported_at": row[4].isoformat(),
                "status": row[5],
            }
            for row in cur.fetchall()
        ]

    return FinanceDashboard(
        transaction_count=int(count),
        calculation_target_count=int(calculation_target_count),
        start_date=(
            actual_start_date.isoformat() if actual_start_date else None
        ),
        end_date=(actual_end_date.isoformat() if actual_end_date else None),
        income_total=int(income_total),
        expense_total=int(expense_total),
        net_total=int(income_total) - int(expense_total),
        monthly=monthly,
        categories=categories,
        recent_rows=recent_rows,
        import_batches=import_batches,
        page=effective_page,
        page_size=recent_limit,
        total_pages=total_pages,
        sort_by=sort_by,
        sort_dir=sort_dir,
        row_mode=row_mode,
    )


def finance_filter_options(db) -> dict[str, list[str]]:
    _guard_db(db)
    with db.cursor() as cur:
        cur.execute(
            """SELECT external_name
               FROM secretary.finance_accounts
               WHERE source_system=%s
               ORDER BY external_name""",
            (SOURCE_SYSTEM,),
        )
        accounts = [row[0] for row in cur.fetchall()]
        cur.execute(
            """SELECT DISTINCT major_name
               FROM secretary.finance_categories
               WHERE source_system=%s AND major_name <> ''
               ORDER BY major_name""",
            (SOURCE_SYSTEM,),
        )
        major_categories = [row[0] for row in cur.fetchall()]
    return {
        "accounts": accounts,
        "major_categories": major_categories,
    }


def _existing_hashes(db, external_ids: list[str]) -> dict[str, str]:
    if not external_ids:
        return {}
    with db.cursor() as cur:
        cur.execute(
            """SELECT external_id, current_content_hash
               FROM secretary.finance_transactions
               WHERE source_system=%s AND external_id = ANY(%s)""",
            (SOURCE_SYSTEM, external_ids),
        )
        return {row[0]: row[1] for row in cur.fetchall()}


def plan_import(
    db,
    preview: FinancePreview,
    csv_bytes: bytes,
) -> ImportPlan:
    _guard_db(db)
    rows = preview.transactions
    external_ids = [
        row["external_id"]
        for row in rows
        if row["external_id"]
    ]
    duplicate_count = len(external_ids) - len(set(external_ids))
    if duplicate_count:
        return ImportPlan(
            source_sha256(csv_bytes),
            len(rows),
            0,
            0,
            0,
            duplicate_count,
        )

    existing = _existing_hashes(db, external_ids)
    inserted = updated = unchanged = 0
    for row in rows:
        external_id = row["external_id"]
        if not external_id:
            raise ValueError(
                "MoneyForward transaction is missing external ID"
            )
        digest = transaction_content_hash(row)
        old = existing.get(external_id)
        if old is None:
            inserted += 1
        elif old == digest:
            unchanged += 1
        else:
            updated += 1
    return ImportPlan(
        source_sha256(csv_bytes),
        len(rows),
        inserted,
        updated,
        unchanged,
        0,
    )


def _get_or_create_account(cur, name: str) -> UUID | None:
    name = name.strip()
    if not name:
        return None
    cur.execute(
        """INSERT INTO secretary.finance_accounts(
               id, source_system, external_name
           )
           VALUES (%s,%s,%s)
           ON CONFLICT (source_system, external_name)
           DO UPDATE SET external_name=EXCLUDED.external_name
           RETURNING id""",
        (uuid4(), SOURCE_SYSTEM, name),
    )
    return cur.fetchone()[0]


def _get_or_create_category(
    cur,
    major: str,
    minor: str,
) -> UUID | None:
    major = major.strip()
    minor = minor.strip()
    if not major and not minor:
        return None
    cur.execute(
        """INSERT INTO secretary.finance_categories(
               id, source_system, major_name, minor_name
           )
           VALUES (%s,%s,%s,%s)
           ON CONFLICT (source_system, major_name, minor_name)
           DO UPDATE SET major_name=EXCLUDED.major_name
           RETURNING id""",
        (uuid4(), SOURCE_SYSTEM, major, minor),
    )
    return cur.fetchone()[0]


def commit_import(
    db,
    preview: FinancePreview,
    csv_bytes: bytes,
    filename: str,
) -> ImportResult:
    _guard_db(db)
    plan = plan_import(db, preview, csv_bytes)
    if plan.duplicate_external_ids:
        return ImportResult(
            "rejected",
            None,
            plan.source_sha256,
            plan.total,
            0,
            0,
            0,
            "duplicate_external_ids_in_source",
        )

    now = datetime.now(timezone.utc)
    with db.transaction():
        with db.cursor() as cur:
            cur.execute(
                """SELECT id
                   FROM secretary.finance_import_batches
                   WHERE source_system=%s AND source_sha256=%s""",
                (SOURCE_SYSTEM, plan.source_sha256),
            )
            prior = cur.fetchone()
            if prior is not None:
                return ImportResult(
                    "replayed",
                    str(prior[0]),
                    plan.source_sha256,
                    plan.total,
                    0,
                    0,
                    plan.total,
                    "source_file_already_imported",
                )

            batch_id = uuid4()
            cur.execute(
                """INSERT INTO secretary.finance_import_batches
                   (id, source_system, source_filename, source_sha256,
                    row_count, imported_at, status)
                   VALUES (%s,%s,%s,%s,%s,%s,'committed')""",
                (
                    batch_id,
                    SOURCE_SYSTEM,
                    filename,
                    plan.source_sha256,
                    plan.total,
                    now,
                ),
            )

            inserted = updated = unchanged = 0
            for row in preview.transactions:
                external_id = row["external_id"].strip()
                digest = transaction_content_hash(row)
                payload = transaction_payload(row)
                account_id = _get_or_create_account(
                    cur,
                    row["account"],
                )
                category_id = _get_or_create_category(
                    cur,
                    row["major_category"],
                    row["minor_category"],
                )

                cur.execute(
                    """SELECT id, current_content_hash
                       FROM secretary.finance_transactions
                       WHERE source_system=%s AND external_id=%s
                       FOR UPDATE""",
                    (SOURCE_SYSTEM, external_id),
                )
                existing = cur.fetchone()

                if existing is None:
                    transaction_id = uuid4()
                    cur.execute(
                        """INSERT INTO secretary.finance_transactions
                           (id, source_system, external_id, transaction_date,
                            description, amount_jpy, account_id, category_id,
                            memo, is_transfer, calculation_target,
                            current_content_hash, first_seen_batch_id,
                            last_seen_batch_id, first_seen_at, last_seen_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                                   %s,%s,%s,%s,%s)""",
                        (
                            transaction_id,
                            SOURCE_SYSTEM,
                            external_id,
                            row["date"],
                            row["content"],
                            int(row["amount"]),
                            account_id,
                            category_id,
                            row["memo"],
                            bool(row["is_transfer"]),
                            bool(row["calculation_target"]),
                            digest,
                            batch_id,
                            batch_id,
                            now,
                            now,
                        ),
                    )
                    inserted += 1
                else:
                    transaction_id, old_hash = existing
                    if old_hash == digest:
                        cur.execute(
                            """UPDATE secretary.finance_transactions
                               SET last_seen_batch_id=%s, last_seen_at=%s
                               WHERE id=%s""",
                            (batch_id, now, transaction_id),
                        )
                        unchanged += 1
                    else:
                        cur.execute(
                            """UPDATE secretary.finance_transactions
                               SET transaction_date=%s, description=%s,
                                   amount_jpy=%s, account_id=%s,
                                   category_id=%s, memo=%s, is_transfer=%s,
                                   calculation_target=%s,
                                   current_content_hash=%s,
                                   last_seen_batch_id=%s, last_seen_at=%s
                               WHERE id=%s""",
                            (
                                row["date"],
                                row["content"],
                                int(row["amount"]),
                                account_id,
                                category_id,
                                row["memo"],
                                bool(row["is_transfer"]),
                                bool(row["calculation_target"]),
                                digest,
                                batch_id,
                                now,
                                transaction_id,
                            ),
                        )
                        updated += 1

                cur.execute(
                    """INSERT INTO secretary.finance_transaction_versions
                       (id, transaction_id, source_system, external_id,
                        source_batch_id, content_hash, payload, observed_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
                       ON CONFLICT (
                         source_system, external_id, content_hash
                       ) DO NOTHING""",
                    (
                        uuid4(),
                        transaction_id,
                        SOURCE_SYSTEM,
                        external_id,
                        batch_id,
                        digest,
                        json.dumps(payload, ensure_ascii=False),
                        now,
                    ),
                )

    return ImportResult(
        "committed",
        str(batch_id),
        plan.source_sha256,
        plan.total,
        inserted,
        updated,
        unchanged,
        None,
    )


class PostgresFinanceRepository:
    def __init__(self, connection_factory):
        self._connection_factory = connection_factory

    def load_finance_dashboard(self, **kwargs) -> FinanceDashboard:
        with self._connection_factory() as db:
            return load_finance_dashboard(db, **kwargs)

    def finance_filter_options(self) -> dict[str, list[str]]:
        with self._connection_factory() as db:
            return finance_filter_options(db)

    def plan_import(
        self,
        preview: FinancePreview,
        csv_bytes: bytes,
    ) -> ImportPlan:
        with self._connection_factory() as db:
            return plan_import(db, preview, csv_bytes)

    def commit_import(
        self,
        preview: FinancePreview,
        csv_bytes: bytes,
        filename: str,
    ) -> ImportResult:
        with self._connection_factory() as db:
            return commit_import(
                db,
                preview,
                csv_bytes,
                filename,
            )
