"""Deterministic MoneyForward import into the isolated prototype DB.

No LLM is used. The CSV bytes are not stored in PostgreSQL; provenance is kept
with filename + SHA-256 + import batch, while normalized transactions keep
stable external IDs and append version rows when source content changes.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4

from .finance_preview import FinancePreview


SOURCE_SYSTEM = "moneyforward_me"
EXPECTED_DB = "secretary_pkb_proto_20260927"
EXPECTED_USER = "secretary_pkb_proto_writer_20260927"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


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


def _guard_db(db) -> None:
    info = db.info
    if (info.dbname or "") != EXPECTED_DB:
        raise ValueError("Refusing non-prototype finance database")
    if (info.user or "") != EXPECTED_USER:
        raise ValueError("Refusing non-dedicated finance writer")
    if (info.host or "") not in LOCAL_HOSTS:
        raise ValueError("Refusing non-local finance database")


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


def plan_import(db, preview: FinancePreview, csv_bytes: bytes) -> ImportPlan:
    _guard_db(db)
    rows = preview.transactions
    external_ids = [r["external_id"] for r in rows if r["external_id"]]
    duplicate_count = len(external_ids) - len(set(external_ids))
    if duplicate_count:
        return ImportPlan(source_sha256(csv_bytes), len(rows), 0, 0, 0, duplicate_count)

    existing = _existing_hashes(db, external_ids)
    inserted = updated = unchanged = 0
    for row in rows:
        ext = row["external_id"]
        if not ext:
            raise ValueError("MoneyForward transaction is missing external ID")
        digest = transaction_content_hash(row)
        old = existing.get(ext)
        if old is None:
            inserted += 1
        elif old == digest:
            unchanged += 1
        else:
            updated += 1
    return ImportPlan(source_sha256(csv_bytes), len(rows), inserted, updated, unchanged, 0)


def _get_or_create_account(cur, name: str) -> UUID | None:
    name = name.strip()
    if not name:
        return None
    cur.execute(
        """INSERT INTO secretary.finance_accounts(id, source_system, external_name)
           VALUES (%s,%s,%s)
           ON CONFLICT (source_system, external_name)
           DO UPDATE SET external_name=EXCLUDED.external_name
           RETURNING id""",
        (uuid4(), SOURCE_SYSTEM, name),
    )
    return cur.fetchone()[0]


def _get_or_create_category(cur, major: str, minor: str) -> UUID | None:
    major = major.strip()
    minor = minor.strip()
    if not major and not minor:
        return None
    cur.execute(
        """INSERT INTO secretary.finance_categories(id, source_system, major_name, minor_name)
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
            "rejected", None, plan.source_sha256, plan.total, 0, 0, 0,
            "duplicate_external_ids_in_source",
        )

    now = datetime.now(timezone.utc)
    with db.transaction():
        with db.cursor() as cur:
            cur.execute(
                """SELECT id FROM secretary.finance_import_batches
                   WHERE source_system=%s AND source_sha256=%s""",
                (SOURCE_SYSTEM, plan.source_sha256),
            )
            prior = cur.fetchone()
            if prior is not None:
                return ImportResult(
                    "replayed", str(prior[0]), plan.source_sha256, plan.total,
                    0, 0, plan.total, "source_file_already_imported",
                )

            batch_id = uuid4()
            cur.execute(
                """INSERT INTO secretary.finance_import_batches
                   (id, source_system, source_filename, source_sha256, row_count, imported_at, status)
                   VALUES (%s,%s,%s,%s,%s,%s,'committed')""",
                (batch_id, SOURCE_SYSTEM, filename, plan.source_sha256, plan.total, now),
            )

            inserted = updated = unchanged = 0
            for row in preview.transactions:
                ext = row["external_id"].strip()
                digest = transaction_content_hash(row)
                payload = transaction_payload(row)
                account_id = _get_or_create_account(cur, row["account"])
                category_id = _get_or_create_category(
                    cur, row["major_category"], row["minor_category"]
                )

                cur.execute(
                    """SELECT id, current_content_hash
                       FROM secretary.finance_transactions
                       WHERE source_system=%s AND external_id=%s
                       FOR UPDATE""",
                    (SOURCE_SYSTEM, ext),
                )
                existing = cur.fetchone()

                if existing is None:
                    tx_id = uuid4()
                    cur.execute(
                        """INSERT INTO secretary.finance_transactions
                           (id, source_system, external_id, transaction_date, description,
                            amount_jpy, account_id, category_id, memo, is_transfer,
                            calculation_target, current_content_hash,
                            first_seen_batch_id, last_seen_batch_id, first_seen_at, last_seen_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (
                            tx_id, SOURCE_SYSTEM, ext, row["date"], row["content"],
                            int(row["amount"]), account_id, category_id, row["memo"],
                            bool(row["is_transfer"]), bool(row["calculation_target"]),
                            digest, batch_id, batch_id, now, now,
                        ),
                    )
                    inserted += 1
                else:
                    tx_id, old_hash = existing
                    if old_hash == digest:
                        cur.execute(
                            """UPDATE secretary.finance_transactions
                               SET last_seen_batch_id=%s, last_seen_at=%s
                               WHERE id=%s""",
                            (batch_id, now, tx_id),
                        )
                        unchanged += 1
                    else:
                        cur.execute(
                            """UPDATE secretary.finance_transactions
                               SET transaction_date=%s, description=%s, amount_jpy=%s,
                                   account_id=%s, category_id=%s, memo=%s, is_transfer=%s,
                                   calculation_target=%s, current_content_hash=%s,
                                   last_seen_batch_id=%s, last_seen_at=%s
                               WHERE id=%s""",
                            (
                                row["date"], row["content"], int(row["amount"]),
                                account_id, category_id, row["memo"], bool(row["is_transfer"]),
                                bool(row["calculation_target"]), digest, batch_id, now, tx_id,
                            ),
                        )
                        updated += 1

                cur.execute(
                    """INSERT INTO secretary.finance_transaction_versions
                       (id, transaction_id, source_system, external_id, source_batch_id,
                        content_hash, payload, observed_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
                       ON CONFLICT (source_system, external_id, content_hash) DO NOTHING""",
                    (
                        uuid4(), tx_id, SOURCE_SYSTEM, ext, batch_id, digest,
                        json.dumps(payload, ensure_ascii=False), now,
                    ),
                )

    return ImportResult(
        "committed", str(batch_id), plan.source_sha256, plan.total,
        inserted, updated, unchanged, None,
    )
